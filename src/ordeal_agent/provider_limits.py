"""Opt-in provider backpressure. Never retry arbitrary side-effectful tool calls.

One limiter coordinates requests inside one process/event loop. Share a gateway
or external quota service for account-wide limits across several workers.
"""
from __future__ import annotations
import asyncio
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random
import time
from typing import Any
import httpx

@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = .25
    max_delay: float = 60.0
    def __post_init__(self):
        if self.max_attempts < 1 or not 0 <= self.base_delay <= self.max_delay or not math.isfinite(self.max_delay):
            raise ValueError('Invalid retry policy')

class ProviderLimiter:
    def __init__(self, *, concurrency: int = 4, requests_per_second: float = 2,
                 tokens_per_minute: int | None = None, adaptive: bool = True):
        if concurrency < 1 or requests_per_second <= 0 or not math.isfinite(requests_per_second):
            raise ValueError('Positive concurrency and request rate required')
        if tokens_per_minute is not None and tokens_per_minute < 1:
            raise ValueError('tokens_per_minute must be positive')
        self.maximum = concurrency
        self.limit = concurrency
        self.active = 0
        self.interval = 1 / requests_per_second
        self.token_limit = tokens_per_minute
        self.adaptive = adaptive
        self._tokens: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()
        self._next = 0.0
        self._paused = 0.0
        self._successes = 0
        self.attempts = 0
        self.throttles = 0

    @asynccontextmanager
    async def slot(self, reserve_tokens: int = 0):
        if type(reserve_tokens) is not int or reserve_tokens < 0:
            raise ValueError('reserve_tokens must be a nonnegative integer')
        if self.token_limit is not None and reserve_tokens > self.token_limit:
            raise ValueError('A request exceeds the entire token-per-minute limit')
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._tokens and self._tokens[0][0] <= now - 60:
                    self._tokens.popleft()
                wait = max(0, self._next-now, self._paused-now)
                if self.token_limit is not None and sum(n for _,n in self._tokens)+reserve_tokens > self.token_limit:
                    wait=max(wait,self._tokens[0][0]+60-now)
                if not wait and self.active < self.limit:
                    self.active += 1
                    self._next = now+self.interval
                    if reserve_tokens: self._tokens.append((now,reserve_tokens))
                    break
            await asyncio.sleep(max(.005,min(wait,.2)))
        try:
            yield
        finally:
            async with self._lock:
                self.active -= 1

    async def request(self, client: httpx.AsyncClient, method: str, url: str, *,
                      idempotent: bool = False, retry: RetryPolicy | None = None,
                      reserve_tokens: int = 0, **kwargs: Any) -> httpx.Response:
        """Return a response; caller chooses raise_for_status/decoding.

        Retries require explicit idempotent=True. Use a provider-supported
        idempotency key where available; POST retry can still incur charges.
        Cancellation is propagated without converting it into a retry.
        """
        policy=retry or RetryPolicy()
        attempts=policy.max_attempts if idempotent else 1
        for attempt in range(attempts):
            response = None
            async with self.slot(reserve_tokens):
                self.attempts += 1
                try:
                    response=await client.request(method,url,**kwargs)
                except (httpx.TimeoutException,httpx.NetworkError):
                    if attempt+1 == attempts: raise
                else:
                    if response.status_code not in {408,429,500,502,503,504}:
                        if response.is_success and self.adaptive:
                            async with self._lock:
                                self._successes += 1
                                if self._successes >= 10:
                                    self.limit=min(self.maximum,self.limit+1);self._successes=0
                        return response
            delay=min(policy.max_delay,policy.base_delay*2**attempt*random.uniform(.5,1.5))
            if response is not None and response.status_code == 429:
                delay=min(policy.max_delay,max(delay,self._retry_after(response.headers.get('retry-after'))))
                async with self._lock:
                    self.throttles += 1
                    self._paused=max(self._paused,time.monotonic()+delay)
                    if self.adaptive: self.limit=max(1,self.limit//2);self._successes=0
            if attempt+1 == attempts:
                assert response is not None
                return response
            if response is not None: await response.aclose()
            await asyncio.sleep(delay)
        raise AssertionError('unreachable')

    @staticmethod
    def _retry_after(value: str | None) -> float:
        if not value: return 0
        try:
            seconds=float(value)
            return max(0,seconds) if math.isfinite(seconds) else 0
        except ValueError:
            try:
                moment=parsedate_to_datetime(value)
                if moment.tzinfo is None: moment=moment.replace(tzinfo=timezone.utc)
                return max(0,(moment-datetime.now(timezone.utc)).total_seconds())
            except (ValueError,TypeError,OverflowError): return 0
