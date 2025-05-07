import datetime
import gc
import hashlib
import importlib
import json
import logging
import os
import signal
from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

from bespokelabs.curator.request_processor.event_loop import run_in_event_loop
from bespokelabs.curator.types.generic_response import GenericRequest, GenericResponse
from tests.integrations import helper

##############################
# Online                     #
##############################


def _hash_string(input_string):
    return hashlib.sha256(input_string.encode("utf-8")).hexdigest()


_ONLINE_REASONING_BACKENDS = [{"integration": backend} for backend in {"anthropic"}]
_ONLINE_BACKENDS = [{"integration": backend} for backend in {"openai", "litellm"}]
_ONLINE_CONCURRENT_ONLY_BACKENDS = [{"integration": backend} for backend in {"litellm/deepinfra"}]
_FAILED_BATCH_BACKENDS = [{"integration": backend, "cached_working_dir": True} for backend in {"anthropic", "openai"}]
_BATCH_BACKENDS = [{"integration": backend} for backend in {"anthropic", "openai"}]


class TimeoutError(Exception):
    pass


class Timeout:
    def __init__(self, seconds):
        self.seconds = seconds

    def __enter__(self):
        signal.signal(signal.SIGALRM, self._handle_timeout)
        signal.alarm(self.seconds)

    def __exit__(self, exc_type, exc_value, traceback):
        signal.alarm(0)

    @staticmethod
    def _handle_timeout(signum, frame):
        raise TimeoutError("Function execution exceeded time limit!")


@pytest.mark.parametrize("temp_working_dir", (_ONLINE_BACKENDS), indirect=True)
def test_basic_without_dataset(temp_working_dir):
    temp_working_dir, backend, vcr_config = temp_working_dir
    hash_book = {
        "openai": "d52319f1976f937ff24f9d53e9c773f37f587dc2fa0d4a4da355e41e5c1eb500",
        "litellm": "6d0d46117661a8c0e725eb83c9299c3cbad38bbfe236715f99d69432423e5787",
    }

    # Test string prompt
    with vcr_config.use_cassette("basic_completion_without_dataset.yaml"):
        # Capture the output to verify status tracker
        output = StringIO()
        console = Console(file=output, width=300)

        dataset = helper.create_basic(temp_working_dir, mock_dataset=None, backend=backend, tracker_console=console, model="gpt-4o-mini")

        # Verify status tracker output
        captured = output.getvalue()
        assert "gpt-4o-mini" in captured, captured
        assert "3" in captured, captured  # Verify total requests processed
        assert "Final Curator Statistics" in captured, captured
        # Verify response content
        recipes = "".join([recipe[0] for recipe in dataset.to_pandas().values.tolist()])
        assert _hash_string(recipes) == hash_book[backend]


@pytest.mark.parametrize("temp_working_dir", (_ONLINE_BACKENDS), indirect=True)
def test_basic_without_dataset_raw_prompt(temp_working_dir):
    temp_working_dir, backend, vcr_config = temp_working_dir
    hash_book = {
        "openai": "d52319f1976f937ff24f9d53e9c773f37f587dc2fa0d4a4da355e41e5c1eb500",
        "litellm": "6d0d46117661a8c0e725eb83c9299c3cbad38bbfe236715f99d69432423e5787",
    }

    # Test raw prompt i.e list of dictionaries
    with vcr_config.use_cassette("basic_completion_without_dataset.yaml"):
        dataset = helper.create_basic(temp_working_dir, mock_dataset=None, backend=backend, model="gpt-4o-mini", raw_prompt=True)
        # Verify response content
        recipes = "".join([recipe[0] for recipe in dataset.to_pandas().values.tolist()])
        assert _hash_string(recipes) == hash_book[backend]


@pytest.mark.parametrize("temp_working_dir", (_ONLINE_BACKENDS), indirect=True)
def test_basic(temp_working_dir, mock_dataset):
    temp_working_dir, backend, vcr_config = temp_working_dir
    hash_book = {
        "openai": "278b2dc5bdf4d2dc1aa18ddb61e37885a9b4aec209bae3bbb81691391ec58692",
        "litellm": "860cbb30c8d65203c54c69fb4e65323d570d784bff98d9dbca27e69316b8fdba",
    }

    with vcr_config.use_cassette("basic_completion.yaml"):
        # Capture the output to verify status tracker
        output = StringIO()
        console = Console(file=output, width=300)

        dataset = helper.create_basic(
            temp_working_dir,
            mock_dataset,
            backend=backend,
            tracker_console=console,
        )

        # Verify status tracker output
        captured = output.getvalue()
        assert "gpt-3.5-turbo" in captured, captured
        assert "3" in captured, captured  # Verify total requests processed
        assert "Final Curator Statistics" in captured, captured
        # Verify response content
        recipes = [recipe[0] for recipe in dataset.to_pandas().values.tolist()]
        recipes.sort()
        recipes = "".join(recipes)
        assert _hash_string(recipes) == hash_book[backend]


@pytest.mark.parametrize("temp_working_dir", (_ONLINE_REASONING_BACKENDS), indirect=True)
def test_basic_reasoning(temp_working_dir, mock_reasoning_dataset):
    temp_working_dir, backend, vcr_config = temp_working_dir
    hash_book = {
        "anthropic": "ada3f38dafdc03168bca2f354c88da64d21686a931ca31607fcf79c1d95b2813",
    }

    with vcr_config.use_cassette("basic_reasoning_completion.yaml"):
        dataset = helper.create_basic(
            temp_working_dir,
            mock_reasoning_dataset.select(range(2)),
            backend=backend,
            generation_params={"max_tokens": 16000, "thinking": {"type": "enabled", "budget_tokens": 14000}},
            model="claude-3-7-sonnet-20250219",
        )
        # Verify response content
        recipes = "".join([recipe[0] for recipe in dataset.to_pandas().values.tolist()])
        assert _hash_string(recipes) == hash_book[backend]


@pytest.mark.parametrize("temp_working_dir", (_ONLINE_CONCURRENT_ONLY_BACKENDS), indirect=True)
def test_basic_concurrent_only(temp_working_dir, mock_dataset):
    temp_working_dir, backend, vcr_config = temp_working_dir

    with vcr_config.use_cassette("basic_concurrent_completion.yaml"):
        # Capture the output to verify status tracker
        output = StringIO()
        console = Console(file=output, width=300)

        dataset, prompter = helper.create_basic(
            temp_working_dir, mock_dataset, backend=backend, tracker_console=console, model="deepinfra/meta-llama/Llama-2-70b-chat-hf", return_prompter=True
        )

        assert prompter._request_processor.max_requests_per_minute is None
        assert prompter._request_processor.max_tokens_per_minute is None
        assert prompter._request_processor.max_concurrent_requests == 200
        # Verify status tracker output
        captured = output.getvalue()
        msg = "deepinfra/meta-llama/Llama-2-70b-chat-hf"
        assert msg in captured
        assert "3" in captured  # Verify total requests processed
        assert "Final Curator Statistics" in captured, captured
        # Verify response content
        assert len(dataset) == 3


@pytest.mark.skip
@pytest.mark.parametrize("temp_working_dir", (_ONLINE_BACKENDS), indirect=True)
def test_camel(temp_working_dir):
    temp_working_dir, _, vcr_config = temp_working_dir
    with vcr_config.use_cassette("camel_completion.yaml"):
        qa_dataset = helper.create_camel(temp_working_dir)
        assert ["subject", "subsubject", "question", "answer"] == qa_dataset.column_names


@pytest.mark.parametrize("temp_working_dir", ([{"integration": "openai"}]), indirect=True)
def test_basic_cache(caplog, temp_working_dir, mock_dataset):
    temp_working_dir, _, vcr_config = temp_working_dir
    with vcr_config.use_cassette("basic_completion.yaml"):
        distilled_dataset = helper.create_basic(temp_working_dir, mock_dataset)

        # This should use cache
        from bespokelabs.curator.constants import _CACHE_MSG

        logger = "bespokelabs.curator.request_processor.base_request_processor"
        with caplog.at_level(logging.INFO, logger=logger):
            helper.create_basic(temp_working_dir, mock_dataset)
            distilled_dataset.cleanup_cache_files()
            assert f"Using cached output dataset. {_CACHE_MSG}" in caplog.text


@pytest.mark.parametrize("temp_working_dir", ([{"integration": "openai"}]), indirect=True)
def test_cache_with_changed_parse(caplog, temp_working_dir, mock_dataset):
    temp_working_dir, _, vcr_config = temp_working_dir
    with vcr_config.use_cassette("basic_completion.yaml"):
        distilled_dataset = helper.create_basic(temp_working_dir, mock_dataset)

    def new_parse(input, response):
        return {"new_recipe": response}

    logger = "bespokelabs.curator.request_processor.base_request_processor"
    with caplog.at_level(logging.INFO, logger=logger):
        distilled_dataset = helper.create_basic(temp_working_dir, mock_dataset, parse_func=new_parse)
        assert "new_recipe" in distilled_dataset.column_names


@pytest.mark.skip
@pytest.mark.parametrize("temp_working_dir", ([{"integration": "openai"}]), indirect=True)
def test_low_rpm_setting(temp_working_dir, mock_dataset):
    temp_working_dir, _, vcr_config = temp_working_dir
    with vcr_config.use_cassette("basic_completion.yaml"):
        helper.create_basic(temp_working_dir, mock_dataset, llm_params={"max_requests_per_minute": 5})


@pytest.mark.parametrize("temp_working_dir", (_ONLINE_BACKENDS), indirect=True)
def test_auto_rpm(temp_working_dir):
    _, _, vcr_config = temp_working_dir
    with vcr_config.use_cassette("basic_completion.yaml"):
        llm = helper.create_llm()
        assert llm._request_processor.header_based_max_requests_per_minute == 10_000
        assert llm._request_processor.header_based_max_tokens_per_minute == 200_000


@pytest.mark.parametrize("temp_working_dir", (_ONLINE_BACKENDS), indirect=True)
def test_resume(caplog, temp_working_dir, mock_dataset):
    temp_working_dir, _, vcr_config = temp_working_dir
    with vcr_config.use_cassette("basic_resume.yaml"):
        with pytest.raises(TimeoutError):
            with Timeout(5):
                helper.create_basic(temp_working_dir, mock_dataset, llm_params={"max_requests_per_minute": 1})
        # Explicity garbage collect the rich live object.
        gc.collect()

        logger = "bespokelabs.curator.request_processor.online.base_online_request_processor"
        with caplog.at_level(logging.INFO, logger=logger):
            helper.create_basic(temp_working_dir, mock_dataset)
            resume_msg = "Already Completed: 1"
            assert resume_msg in caplog.text


@pytest.mark.parametrize("temp_working_dir", (_ONLINE_BACKENDS), indirect=True)
def test_invalid_failed_reason(caplog, temp_working_dir, mock_dataset):
    temp_working_dir, backend, vcr_config = temp_working_dir

    def _invalid_failed_reason(reason):
        patch.stopall()
        now = datetime.datetime.now()
        output = StringIO()
        console = Console(file=output, width=300)
        request = GenericRequest(model="", messages=[{}], original_row={}, original_row_idx=0)

        # default invalid reason
        invalid_reason_response = GenericResponse(
            finish_reason=reason, generic_request=request, raw_response={"choices": [{"finish_reason": reason}]}, created_at=now, finished_at=now
        )
        logger = "bespokelabs.curator.request_processor.online.base_online_request_processor"
        REASON_MSG = f"Encountered 'ValueError: finish_reason was {reason}' during attempt 1 of 10 while processing request 0"
        if backend == "openai":
            patcher = patch("bespokelabs.curator.request_processor.online.openai_online_request_processor.OpenAIOnlineRequestProcessor.call_single_request")
        else:
            patcher = patch("bespokelabs.curator.request_processor.online.litellm_online_request_processor.LiteLLMOnlineRequestProcessor.call_single_request")

        mock = patcher.start()
        mock.return_value = invalid_reason_response
        try:
            with pytest.raises(TimeoutError):
                with Timeout(3):
                    with caplog.at_level(logging.WARN, logger=logger):
                        llm_params = {"max_requests_per_minute": 1}
                        if reason not in ["content_filter", "length"]:
                            llm_params["invalid_finish_reasons"] = [reason]

                        helper.create_basic(temp_working_dir, mock_dataset, llm_params=llm_params, tracker_console=console, backend=backend)
        finally:
            patcher.stop()
            patch.stopall()
        assert REASON_MSG in caplog.text

    with vcr_config.use_cassette("basic_completion.yaml"):
        # Default
        _invalid_failed_reason("length")
        # Custom
        _invalid_failed_reason("tool_calls")


##############################
# Batch                      #
##############################


def _reload_batch_patch_deps():
    from bespokelabs.curator.request_processor.batch import base_batch_request_processor

    importlib.reload(base_batch_request_processor)


@pytest.mark.parametrize("temp_working_dir", (_BATCH_BACKENDS), indirect=True)
def test_batch_resume(temp_working_dir, mock_dataset):
    temp_working_dir, backend, vcr_config = temp_working_dir
    with vcr_config.use_cassette("basic_batch_resume.yaml"):
        with patch("bespokelabs.curator.request_processor.event_loop.run_in_event_loop") as mocked_run_loop:

            def _run_loop(func):
                if "poll_and_process_batches" in str(func):
                    return
                return run_in_event_loop(func)

            mocked_run_loop.side_effect = _run_loop
            with pytest.raises(ValueError):
                _reload_batch_patch_deps()
                helper.create_basic(temp_working_dir, mock_dataset, batch=True, backend=backend)
        from bespokelabs.curator.status_tracker.batch_status_tracker import BatchStatusTracker

        tracker_batch_file_path = temp_working_dir + "/testing_hash_123/batch_objects.jsonl"
        with open(tracker_batch_file_path, "r") as f:
            tracker = BatchStatusTracker.model_validate_json(f.read())
        assert tracker.n_total_requests == 3
        assert len(tracker.submitted_batches) == 1
        assert len(tracker.downloaded_batches) == 0

        patch.stopall()
        _reload_batch_patch_deps()
        helper.create_basic(temp_working_dir, mock_dataset, batch=True, backend=backend)
        with open(tracker_batch_file_path, "r") as f:
            tracker = BatchStatusTracker.model_validate_json(f.read())
        assert len(tracker.submitted_batches) == 0
        assert len(tracker.downloaded_batches) == 1


@pytest.mark.parametrize("temp_working_dir", (_BATCH_BACKENDS), indirect=True)
def test_batch_cancel(
    caplog,
    temp_working_dir,
    mock_dataset,
):
    os.environ["CURATOR_VIEWER"] = "false"
    os.environ["HOSTED_CURATOR_VIEWER"] = "false"

    temp_working_dir, backend, vcr_config = temp_working_dir
    with vcr_config.use_cassette("batch_cancel.yaml") as cassette:
        with patch("bespokelabs.curator.request_processor.event_loop.run_in_event_loop") as mocked_run_loop:

            def _run_loop(func):
                if "poll_and_process_batches" in str(func):
                    return
                return run_in_event_loop(func)

            mocked_run_loop.side_effect = _run_loop
            with pytest.raises(ValueError):
                _reload_batch_patch_deps()
                helper.create_basic(temp_working_dir, mock_dataset, batch=True, backend=backend)
        from bespokelabs.curator.status_tracker.batch_status_tracker import BatchStatusTracker

        tracker_batch_file_path = temp_working_dir + "/testing_hash_123/batch_objects.jsonl"
        with open(tracker_batch_file_path, "r") as f:
            tracker = BatchStatusTracker.model_validate_json(f.read())
        assert tracker.n_total_requests == 3
        assert len(tracker.submitted_batches) == 1
        assert len(tracker.downloaded_batches) == 0

        patch.stopall()
        _reload_batch_patch_deps()
        logger = "bespokelabs.curator.request_processor.batch.base_batch_request_processor"
        with caplog.at_level(logging.INFO, logger=logger):
            helper.create_basic(temp_working_dir, mock_dataset, batch=True, backend=backend, batch_cancel=True)
            resume_msg = "Cancelling batches"
            helper.assert_all_requests_played(cassette)

            assert resume_msg in caplog.text


@pytest.mark.parametrize("temp_working_dir", (_ONLINE_REASONING_BACKENDS), indirect=True)
def test_batch_reasoning(temp_working_dir, mock_reasoning_dataset):
    temp_working_dir, backend, vcr_config = temp_working_dir
    hash_book = {
        "anthropic": "ada3f38dafdc03168bca2f354c88da64d21686a931ca31607fcf79c1d95b2813",
    }

    with vcr_config.use_cassette("basic_batch_reasoning_completion.yaml"):
        dataset = helper.create_basic(
            temp_working_dir,
            mock_reasoning_dataset.select(range(2)),
            backend=backend,
            generation_params={"max_tokens": 16000, "thinking": {"type": "enabled", "budget_tokens": 14000}},
            model="claude-3-7-sonnet-20250219",
            batch=True,
            batch_check_interval=1,
        )
        # Verify response content
        recipes = "".join([recipe[0] for recipe in dataset.to_pandas().values.tolist()])
        assert _hash_string(recipes) == hash_book[backend]


@pytest.mark.parametrize("temp_working_dir", (_FAILED_BATCH_BACKENDS), indirect=True)
def test_failed_request_in_batch_resume(caplog, temp_working_dir, mock_dataset):
    temp_working_dir, backend, vcr_config = temp_working_dir
    with vcr_config.use_cassette("failed_request_batch_resume.yaml"):
        tracker_batch_file_path = temp_working_dir + "/testing_hash_123/batch_objects.jsonl"

        from bespokelabs.curator.status_tracker.batch_status_tracker import BatchStatusTracker

        with open(tracker_batch_file_path, "r") as f:
            failed_tracker = BatchStatusTracker.model_validate_json(f.read())
        assert failed_tracker.n_total_requests == 3
        assert failed_tracker.n_downloaded_failed_requests == 1
        assert len(failed_tracker.submitted_batches) == 0
        assert len(failed_tracker.downloaded_batches) == 1
        RESUBMIT_MSG = f"Request file tests/integrations/{backend}/fixtures/.test_cache/testing_hash_123/requests_0.jsonl is being re-submitted."

        logger = "bespokelabs.curator.status_tracker.batch_status_tracker"

        patcher = patch("bespokelabs.curator.db.MetadataDB.validate_schema")

        mock = patcher.start()
        mock.return_value = None
        with caplog.at_level(logging.INFO, logger=logger):
            helper.create_basic(temp_working_dir, mock_dataset, batch=True, backend=backend)
            assert RESUBMIT_MSG in caplog.text
        patcher.stop()
        with open(tracker_batch_file_path, "r") as f:
            tracker = BatchStatusTracker.model_validate_json(f.read())
        assert len(tracker.submitted_batches) == 0
        resubmitted_sucess_batch = [key for key in tracker.downloaded_batches.keys() if key not in failed_tracker.downloaded_batches.keys()][0]
        assert tracker.downloaded_batches[resubmitted_sucess_batch].request_counts.total == 1
        assert tracker.downloaded_batches[resubmitted_sucess_batch].request_counts.succeeded == 1


@pytest.mark.parametrize("temp_working_dir", (_BATCH_BACKENDS), indirect=True)
def test_basic_batch(temp_working_dir, mock_dataset):
    temp_working_dir, backend, vcr_config = temp_working_dir
    hash_book = {
        "openai": "47127d9dcb428c18e5103dffcb0406ba2f9acab2f1ea974606962caf747b0ad5",
        "anthropic": "f38e7406448e95160ebe4d9b6148920ef37b019f23a4e2c57094fdd4bafb09be",
    }
    with vcr_config.use_cassette("basic_batch_completion.yaml"):
        output = StringIO()
        console = Console(file=output, width=300)

        dataset = helper.create_basic(temp_working_dir, mock_dataset, batch=True, backend=backend, tracker_console=console)
        recipes = "".join([recipe[0] for recipe in dataset.to_pandas().values.tolist()])
        assert _hash_string(recipes) == hash_book[backend]

        # Verify status tracker output
        captured = output.getvalue()
        assert "Batches: Total: 1 • Submitted: 0⋯ • Downloaded: 1✓" in captured, captured
        assert "Requests: Total: 3 • In Progress: 0⋯ • Succeeded: 3✓ • Failed: 0✗" in captured, captured
        assert "Final Curator Statistics" in captured, captured
        assert "Total Requests             │ 3" in captured, captured
        assert "Successful                 │ 3" in captured, captured
        assert "Failed                     │ 0" in captured, captured


@pytest.mark.parametrize("temp_working_dir", ([{"integration": "openai"}]), indirect=True)
def test_batch_resubmission(caplog, temp_working_dir, mock_dataset):
    """
    Following test case is to verify the resubmission of failed requests in batch completion.
    1. Create a batch completion with a dataset containing 3 requests.
    2. VCR returns a response with 1 failed request i.e num_failed_requests = 1.
    3. Verify the resubmission of failed request.
    4. VCR returns the response of resubmitted request with invalid finish reason i.e length.
    5. Again verify the resubmission of failed request.
    """
    temp_working_dir, backend, vcr_config = temp_working_dir
    hash_book = {
        "openai": "58a8857e3752388c8bb2c625be0f4329c77298b58c26aa8005ea1c3a3b6a822e",
    }
    with vcr_config.use_cassette("resubmission_batch_completion.yaml"):
        output = StringIO()
        console = Console(file=output, width=300)

        logger = "bespokelabs.curator.request_processor.batch.base_batch_request_processor"
        with caplog.at_level(logging.WARNING, logger=logger):
            dataset = helper.create_basic(temp_working_dir, mock_dataset, batch=True, backend=backend, tracker_console=console)
        recipes = "".join([recipe[0] for recipe in dataset.to_pandas().values.tolist()])
        assert _hash_string(recipes) == hash_book[backend]

        # Verify status tracker output
        captured = output.getvalue()

        # Verify resubmission message
        msg = "Request file tests/integrations/openai/fixtures/.test_cache/testing_hash_123/requests_0.jsonl is being re-submitted."
        assert "has failed requests. Tagging for resubmission." in caplog.text
        assert msg in caplog.text
        assert "Invalid finish responses: {'length': 1}" in caplog.text

        assert "Batches: Total: 3 • Submitted: 0⋯ • Downloaded: 3✓" in captured, captured
        assert "Requests: Total: 3 • In Progress: 0⋯ • Succeeded: 4✓ • Failed: 1✗" in captured, captured
        assert "Final Curator Statistics" in captured, captured
        assert "Total Requests             │ 3" in captured, captured
        assert "Successful                 │ 4" in captured, captured
        assert "Failed                     │ 1" in captured, captured


@pytest.mark.parametrize("temp_working_dir", ([{"integration": "openai"}]), indirect=True)
def test_failed_requests_file_in_cache(temp_working_dir, mock_dataset):
    temp_working_dir, backend, vcr_config = temp_working_dir
    with vcr_config.use_cassette("resubmission_batch_completion.yaml"):
        output = StringIO()
        console = Console(file=output, width=300)
        helper.create_basic(
            temp_working_dir, mock_dataset, batch=True, backend=backend, tracker_console=console, llm_params={"max_retries": 0, "require_all_responses": False}
        )
        with open("tests/integrations/openai/fixtures/.test_cache/testing_hash_123/failed_requests.jsonl", "r") as f:
            lines = f.readlines()
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["original_row_idx"] == 2


##############################
# Offline                    #
##############################


@pytest.mark.parametrize("temp_working_dir", ([{"integration": "vllm"}]), indirect=True)
def test_basic_offline(temp_working_dir, mock_dataset):
    """Test basic completion with VLLM backend"""
    temp_working_dir, _, _ = temp_working_dir

    import json
    import os

    # Load mock responses from fixture file
    fixture_path = os.path.join(os.path.dirname(__file__), "vllm", "fixtures", "basic_responses.json")
    with open(fixture_path) as f:
        mock_responses = json.load(f)

    # Mock the vllm.LLM.generate method based on replay output
    class MockVLLMOutput:
        def __init__(self, text, request_id):
            self.text = text
            self.request_id = request_id
            self.finished = True
            self.prompt = None  # From replay output
            self.encoder_prompt = None
            self.metrics = None
            self.parsed_response = None

        @property
        def outputs(self):
            return [type("MockOutput", (), {"text": self.text})]

    def mock_generate(prompts, sampling_params):
        """Mock the generate method based on replay output"""
        assert len(prompts) == 3  # Verify batch size
        # Verify prompts match the expected format
        template = (
            "<|im_start|>system\n"
            "You are Qwen, created by Alibaba Cloud. You are a helpful assistant."
            "<|im_end|>\n"
            "<|im_start|>user\n{}\n"
            "<|im_end|>\n"
            "<|im_start|>assistant\n"
        )
        for i, prompt in enumerate(prompts):
            assert prompt == template.format(mock_dataset[i]["dish"])

        return [MockVLLMOutput(mock_responses[str(i)], i) for i in range(len(prompts))]

    def mock_apply_chat_template(conversation=None, tokenize=None, add_generation_prompt=None, **kwargs):
        """Mock the tokenizer's apply_chat_template method"""
        assert len(conversation) == 1  # We expect single message per prompt
        assert conversation[0]["role"] == "user"
        template = (
            "<|im_start|>system\n"
            "You are Qwen, created by Alibaba Cloud. You are a helpful assistant."
            "<|im_end|>\n"
            "<|im_start|>user\n{}\n"
            "<|im_end|>\n"
            "<|im_start|>assistant\n"
        )
        return template.format(conversation[0]["content"])

    # Mock CUDA-related methods
    mock_cuda = type(
        "MockCuda",
        (),
        {
            "synchronize": lambda: None,
            "empty_cache": lambda: None,
            "is_available": lambda: True,
            "get_device_name": lambda device: "Mock GPU",
            "device_count": lambda: 1,
        },
    )

    with (
        patch("vllm.LLM") as mock_llm,
        patch("torch.cuda", mock_cuda),
        patch("torch.cuda.synchronize"),
        patch("torch.cuda.empty_cache"),
    ):
        mock_llm.return_value.generate = mock_generate
        mock_llm.return_value.get_tokenizer.return_value.apply_chat_template = mock_apply_chat_template

        dataset = helper.create_basic(
            temp_working_dir,
            mock_dataset,
            backend="vllm",
        )

        # Verify response content
        recipes = "".join([recipe[0] for recipe in dataset.to_pandas().values.tolist()])
        assert _hash_string(recipes) == "f0e229cb0b9c6d60930abda07998fe5870c7e94331ca877af8f400f9697213ee"
