"""Tiny external-agent example. Run with: uvicorn main:app --port 9000"""
from fastapi import FastAPI
import httpx
app=FastAPI()
@app.post('/dispatch')
async def dispatch(envelope:dict):
    proxy=envelope['tool_proxy_url'];v=envelope.get('variables',{})
    async with httpx.AsyncClient() as c:
        order=await c.post(proxy+'/get_order',json={'id':v.get('order_id','o1')})
        payment=await c.post(proxy+'/get_payment',json={'id':v.get('payment_id','p1')})
        if 'error' not in payment.json():
            await c.post(proxy+'/create_refund',json={'payment_id':v.get('payment_id','p1'),'amount':payment.json().get('amount',0)})
    return {'final_response':'Handled refund request'}
