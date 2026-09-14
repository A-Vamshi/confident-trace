"""Verify published metadata and init() in an isolated base-only installation."""

import os
import subprocess
import tempfile
import venv
import zipfile
from email.parser import BytesParser
from pathlib import Path

from packaging.requirements import Requirement

root = Path(__file__).resolve().parents[1]
wheel = max((root / "python/dist").glob("*.whl"), key=lambda p: p.stat().st_mtime)
with zipfile.ZipFile(wheel) as archive:
    name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
    metadata = BytesParser().parsebytes(archive.read(name))
    assert metadata.get_all("Provides-Extra") == ["openai-agents"]
    requirements = [
        Requirement(value) for value in metadata.get_all("Requires-Dist", [])
    ]
    required = {
        item.name
        for item in requirements
        if item.marker is None or item.marker.evaluate({"extra": ""})
    }
    assert "opentelemetry-instrumentation-asgi" in required
    assert "openinference-instrumentation-openai-agents" not in required
    optional = {
        item.name
        for item in requirements
        if item.marker is not None
        and item.marker.evaluate({"extra": "openai-agents"})
        and not item.marker.evaluate({"extra": ""})
    }
    assert optional == {"openinference-instrumentation-openai-agents"}

# Do not inherit tracing configuration or source-tree imports from the host.
env = {
    k: v
    for k, v in os.environ.items()
    if not k.startswith(("OTEL_", "CONFIDENT_", "PYTHONPATH", "PYTHONHOME"))
}
smoke = """
from importlib.metadata import distributions
from confident_trace import init, shutdown, span, flush
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
installed = {d.metadata['Name'].lower().replace('_', '-') for d in distributions()}
assert not installed.intersection({
    'litellm', 'openai', 'anthropic', 'google-genai', 'boto3', 'pytest',
    'openrouter', 'portkey-ai', 'openai-agents', 'bedrock-agentcore',
    'openinference-instrumentation-openai-agents',
})
assert 'opentelemetry-instrumentation-asgi' in installed
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
assert callable(OpenTelemetryMiddleware)
# Construct both real exporters with all default integrations enabled.
assert init().active
assert shutdown()
assert init(protocol='grpc', endpoint='http://localhost:4317').active
assert shutdown()
sink = InMemorySpanExporter()
assert init(exporter=sink).active
with span('base-install'):
    pass
assert flush()
assert len(sink.get_finished_spans()) == 1
assert shutdown()
print('Base-only Python init, HTTP/gRPC exporters, span, flush and shutdown passed')
"""
with tempfile.TemporaryDirectory(prefix="confident-python-install-") as directory:
    target = Path(directory)
    venv.EnvBuilder(with_pip=True).create(target / "venv")
    python = (
        target / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    subprocess.run(
        [str(python), "-m", "pip", "install", str(wheel)], check=True, env=env
    )
    subprocess.run([str(python), "-c", smoke], cwd=target, check=True, env=env)

    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            f"confident-trace[openai-agents] @ {wheel.as_uri()}",
        ],
        check=True,
        env=env,
    )
    subprocess.run(
        [
            str(python),
            "-c",
            """
from importlib.metadata import distribution, PackageNotFoundError
from confident_trace import init, shutdown
assert distribution('openinference-instrumentation-openai-agents')
try:
    distribution('openai-agents')
except PackageNotFoundError:
    pass
else:
    raise AssertionError('The bridge extra must not install the application SDK')
assert init().active
assert shutdown()
print('OpenAI Agents extra installs its bridge without an SDK or startup crash')
""",
        ],
        cwd=target,
        check=True,
        env=env,
    )
