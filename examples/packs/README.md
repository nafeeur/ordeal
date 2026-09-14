# Curated, local-only scenario packs

These inspectable JSON packs provision deterministic evaluators and test definitions. They do not execute code or send network requests. Cases are starting points, not compliance certifications or comprehensive safety coverage. The payment ordering check concerns observed ordering; it does not independently prove that a payment was authorized for the correct entity.

```bash
cd examples/packs
sha256sum -c SHA256SUMS
ordeal-server add-pack payment-authorization.json --sha256 "$(sha256sum payment-authorization.json | cut -d ' ' -f 1)"
```

Set server/project/token environment variables first. For an independently trusted pack, obtain the expected hash through a separate trusted channel; computing a hash from an untrusted download only detects subsequent changes, not authenticity. This is a curated file library, not a hosted marketplace or package-signing service.
