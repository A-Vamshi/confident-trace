"""A real JobContext cleanup in a worker that exits before the batch timer fires."""

import asyncio
import json
import multiprocessing
import sys
from pathlib import Path
from types import SimpleNamespace

from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult


class FileExporter(SpanExporter):
    def __init__(self, path):
        self.path = path

    def export(self, spans):
        with open(self.path, "a") as output:
            for span in spans:
                output.write(
                    json.dumps(
                        {
                            "name": span.name,
                            "id": span.context.span_id,
                            "parent": span.parent.span_id if span.parent else None,
                        }
                    )
                    + "\n"
                )
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


def child(path):
    from livekit.agents import JobContext, telemetry

    import confident_trace as ct

    ct.init(exporter=FileExporter(path), instrumentations=("livekit",))

    async def run():
        with telemetry.tracer.start_as_current_span("job_root"):
            with telemetry.tracer.start_as_current_span("agent_session"):
                pass

            async def late_cleanup():
                await asyncio.sleep(0.01)
                with telemetry.tracer.start_as_current_span("late_cleanup"):
                    pass

            await asyncio.gather(late_cleanup())
        # The real worker ends its root before invoking JobContext._on_cleanup.
        ctx = SimpleNamespace(
            _early_log_handler=None,
            _recording_initialized=False,
            _tempdir=SimpleNamespace(cleanup=lambda: None),
            _telemetry_state=None,
            job=SimpleNamespace(id="offline"),
            _handlers_with_filter=[],
        )
        await JobContext._on_cleanup(ctx)

    asyncio.run(run())
    # No ct.flush/shutdown: forkserver will not execute our atexit handlers.


if __name__ == "__main__":
    process = multiprocessing.get_context(sys.argv[1]).Process(
        target=child, args=(sys.argv[2],)
    )
    process.start()
    process.join(40)
    if process.is_alive():
        process.terminate()
        process.join()
        raise AssertionError("Worker did not exit")
    assert process.exitcode == 0
    spans = [json.loads(line) for line in Path(sys.argv[2]).read_text().splitlines()]
    assert {s["name"] for s in spans} == {"job_root", "agent_session", "late_cleanup"}
    ids = {s["id"] for s in spans}
    assert all(s["parent"] is None or s["parent"] in ids for s in spans)
