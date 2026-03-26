"""
states.py — Хранение состояний пользователей в памяти.

VK Long Poll не имеет встроенного FSM как aiogram.
Используем простой in-memory словарь.
Данные сбрасываются при рестарте (для персистентности можно добавить SQLite).
"""

from dataclasses import dataclass, field
from typing import Any

# Состояния
STATE_IDLE              = None
STATE_WAITING_MESSAGE   = "waiting_for_anon_message"
STATE_WAITING_REPLY     = "waiting_for_reply"


@dataclass
class UserState:
    state: str | None = None
    data: dict = field(default_factory=dict)


# Глобальное хранилище: { vk_id: UserState }
_states: dict[int, UserState] = {}


def get_state(vk_id: int) -> UserState:
    if vk_id not in _states:
        _states[vk_id] = UserState()
    return _states[vk_id]


def set_state(vk_id: int, state: str | None, **data):
    us = get_state(vk_id)
    us.state = state
    us.data = data


def clear_state(vk_id: int):
    _states.pop(vk_id, None)


def get_data(vk_id: int) -> dict:
    return get_state(vk_id).data


def current_state(vk_id: int) -> str | None:
    return get_state(vk_id).state