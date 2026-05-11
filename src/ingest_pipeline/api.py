import time
from pathlib import Path

import anthropic

from .state import State, append_journal, journal_event, save_state


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
    time.sleep(60.0 / rpm)

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
        messages=[{"role": "user", "content": user_content}],
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
        ),
    )
    save_state(dest_root, state)

    return response.content[0].text
