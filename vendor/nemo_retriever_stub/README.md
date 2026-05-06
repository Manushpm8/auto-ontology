# nemo_retriever — stub

This is a **stub** of the real [NeMo-Retriever] package, used so that the GSF
backend can build and start in environments (Docker, CI, demo clusters) where
the real package is not vendored.

The stub:

- exposes every symbol that `gsf/server/**` and `gsf/connectors/**` import at
  module load time,
- lets the FastAPI app start cleanly and serve `/api/health`,
- raises `NotImplementedError("nemo_retriever stub: <symbol>")` when any
  runtime function that needs the real implementation is called.

To run with the real package, point the backend Dockerfile at it:

```bash
docker build \
    --build-arg NEMO_RETRIEVER_SRC=path/to/nemo_retriever \
    -t gsf-backend:real .
```

[NeMo-Retriever]: https://gitlab-master.nvidia.com/nemo-project/NeMo-Retriever
