"""
Websocket layer for Twitch PubSub connections.

This module provides websocket connection management for subscribing to
Twitch PubSub topics and receiving real-time updates about drops, channels,
and stream states.

Classes:
    Websocket: Manages a single websocket connection with topic subscriptions
    WebsocketPool: Manages multiple websocket connections for topic distribution
"""

from src.websocket.pool import WebsocketPool
from src.websocket.websocket import Websocket


__all__ = ["Websocket", "WebsocketPool"]
