import base64
import time
from pathlib import Path

import anthropic
from anthropic import RateLimitError, APIStatusError

from .state import State, append_journal, journal_event, save_state


def _call_with_retry(
    client: anthropic.Anthropic,
    model: str,
    system_prompt: str,
    content: str | list,
    max_tokens: int,
    rpm: int,
    state: State,
    dest_root: Path,
) -> str:
    sleep_interval = 60.0 / rpm
    for attempt in range(3):
        time.sleep(sleep_interval)
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": content}],
            )

            state.total_input_tokens += response.usage.input_tokens
            state.total_output_tokens += response.usage.output_tokens

            append_journal(
                dest_root,
                journal_event(
                    "api_call",
                    model=model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cumulative_tokens=state.total_input_tokens + state.total_output_tokens,
                    cmd=state.command,
                ),
            )
            save_state(dest_root, state)

            return response.content[0].text
        except RateLimitError:
            if attempt == 2:
                raise
            sleep_interval = 2 * (60.0 / rpm)
        except APIStatusError as e:
            if e.status_code < 500 or attempt == 2:
                raise
            sleep_interval = 2 * (60.0 / rpm)


def call_claude(
    client: anthropic.Anthropic,
    model: str,
    system_prompt: str,
    user_content: str,
    max_tokens: int,
    rpm: int,
    state: State,
    dest_root: Path,
) -> str:
    return _call_with_retry(
        client=client,
        model=model,
        system_prompt=system_prompt,
        content=user_content,
        max_tokens=max_tokens,
        rpm=rpm,
        state=state,
        dest_root=dest_root,
    )


def call_claude_vision(
    client: anthropic.Anthropic,
    model: str,
    system_prompt: str,
    image_data: bytes,
    media_type: str,
    prompt_text: str,
    max_tokens: int,
    rpm: int,
    state: State,
    dest_root: Path,
) -> str:
    b64_data = base64.standard_b64encode(image_data).decode("ascii")
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64_data}},
        {"type": "text", "text": prompt_text},
    ]
    return _call_with_retry(
        client=client,
        model=model,
        system_prompt=system_prompt,
        content=content,
        max_tokens=max_tokens,
        rpm=rpm,
        state=state,
        dest_root=dest_root,
    )
