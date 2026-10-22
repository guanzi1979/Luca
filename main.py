import os
import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "web_htmx.app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        workers=1,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get(
            "TURNI_TRUSTED_PROXY_IPS", "127.0.0.1"
        ),
    )