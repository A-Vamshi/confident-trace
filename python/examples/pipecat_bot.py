"""Run from python/ with CONFIDENT_API_KEY and OPENAI_API_KEY in .env, then open
http://localhost:7860/client to talk to the bot:
venv/bin/pip install "pipecat-ai[openai,silero,webrtc,runner]"
venv/bin/python examples/pipecat_bot.py
"""

from dotenv import load_dotenv
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.utils import create_transport
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.workers.runner import WorkerRunner

from confident_trace import init

load_dotenv()
init()


async def bot(runner_args):
    transport = await create_transport(
        runner_args,
        {
            "webrtc": lambda: TransportParams(
                audio_in_enabled=True, audio_out_enabled=True
            )
        },
    )
    context = LLMContext(
        messages=[{"role": "system", "content": "You are a helpful voice assistant."}]
    )
    aggregators = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
    )
    pipeline = Pipeline(
        [
            transport.input(),
            OpenAISTTService(),
            aggregators.user(),
            OpenAILLMService(settings=OpenAILLMService.Settings(model="gpt-4.1-mini")),
            OpenAITTSService(),
            transport.output(),
            aggregators.assistant(),
        ]
    )
    # Tracing is on because init() ran; no enable_tracing=True needed.
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        conversation_id=runner_args.session_id,
    )

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    await runner.run()


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
