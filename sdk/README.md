# boldsci-cache-layer

Python SDK for the Bold Science Cache Layer API.

## Installation

```bash
pip install boldsci-cache-layer
```

## Usage

```python
from boldsci_cache_layer import CacheLayerClient

client = CacheLayerClient(api_url="https://...", api_key="your-key")

result = client.lookup(
    workspace_id="ws_123",
    project_id="my-project",
    query="How do I reset my password?",
)

if result.status == "hit":
    print(result.response.content)
```
