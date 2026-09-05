"""finassist package init.

Corporate networks terminate TLS with their own root CA, which Python's bundled certifi
store does not know about, so every LLM call fails with CERTIFICATE_VERIFY_FAILED after a
retry delay. Using the OS trust store fixes it without disabling verification.
"""
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass
