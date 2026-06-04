"""
Turn Timer Microservice  —  Microservice C
Chess Trainer / CS 361

COMMUNICATION PIPE: gRPC over TCP port 50053
─────────────────────────────────────────────────────────────
This service and main.py do NOT import each other.
They communicate exclusively through the gRPC network pipe:

  main.py  ──[StartTurn / CheckTime / EndTurn]──►  server.py (port 50053)
  main.py  ◄─[time remaining / expired flag]──────  server.py

─────────────────────────────────────────────────────────────

Manages a countdown timer per player per game.
Supports configurable time banks, turn start/end, and expiry detection.
Multiple concurrent games are supported via game_id keying.
"""

import grpc
import json
import time
import logging
import threading
from concurrent import futures
from datetime import datetime

import timer_pb2
import timer_pb2_grpc

logging.basicConfig(level=logging.INFO, format="[TIMER] %(message)s")
log = logging.getLogger(__name__)

DEFAULT_SECONDS = 300  # 5 minutes per player if not configured

# ── In-memory game state ───────────────────────────────────────────────────────
# Structure per game_id:
# {
#   "WHITE": { "bank": int, "turn_start": float or None },
#   "BLACK": { "bank": int, "turn_start": float or None },
#   "configured": bool
# }

game_timers = {}
lock = threading.Lock()

def get_or_create(game_id, seconds=DEFAULT_SECONDS):
    if game_id not in game_timers:
        game_timers[game_id] = {
            "WHITE": {"bank": seconds, "turn_start": None},
            "BLACK": {"bank": seconds, "turn_start": None},
            "configured": False,
        }
    return game_timers[game_id]

def seconds_remaining(game_id, color):
    """Return remaining seconds for color, accounting for active turn."""
    with lock:
        g = game_timers.get(game_id)
        if not g:
            return 0
        player = g[color]
        bank = player["bank"]
        if player["turn_start"] is not None:
            elapsed = time.time() - player["turn_start"]
            bank = max(0, bank - int(elapsed))
        return bank

# ── gRPC service ───────────────────────────────────────────────────────────────

class TurnTimerServicer(timer_pb2_grpc.TurnTimerServicer):

    def ConfigTimer(self, request, context):
        log.info(f"Config   game={request.game_id} seconds_per_player={request.seconds_per_player}")
        if request.seconds_per_player <= 0:
            return timer_pb2.ConfigTimerResponse(success=False, error_message="seconds_per_player must be positive")
        with lock:
            game_timers[request.game_id] = {
                "WHITE": {"bank": request.seconds_per_player, "turn_start": None},
                "BLACK": {"bank": request.seconds_per_player, "turn_start": None},
                "configured": True,
            }
        log.info(f"Config OK  game={request.game_id} bank={request.seconds_per_player}s each")
        return timer_pb2.ConfigTimerResponse(success=True)

    def StartTurn(self, request, context):
        color = request.active_color.upper()
        log.info(f"StartTurn game={request.game_id} color={color}")
        with lock:
            g = get_or_create(request.game_id)
            player = g[color]
            if player["bank"] <= 0:
                return timer_pb2.StartTurnResponse(
                    success=False,
                    seconds_remaining=0,
                    error_message=f"{color} has no time remaining",
                )
            player["turn_start"] = time.time()
            remaining = player["bank"]
        log.info(f"StartTurn OK  {color} has {remaining}s in bank")
        return timer_pb2.StartTurnResponse(success=True, seconds_remaining=remaining)

    def CheckTime(self, request, context):
        color = request.active_color.upper()
        remaining = seconds_remaining(request.game_id, color)
        expired = remaining <= 0
        log.info(f"CheckTime game={request.game_id} {color}={remaining}s expired={expired}")
        return timer_pb2.CheckTimeResponse(
            seconds_remaining=remaining,
            time_expired=expired,
            active_color=color,
        )

    def EndTurn(self, request, context):
        color = request.active_color.upper()
        log.info(f"EndTurn  game={request.game_id} color={color}")
        with lock:
            g = game_timers.get(request.game_id)
            if not g:
                return timer_pb2.EndTurnResponse(success=False, seconds_used=0, seconds_remaining=0)
            player = g[color]
            if player["turn_start"] is None:
                return timer_pb2.EndTurnResponse(
                    success=False, seconds_used=0,
                    seconds_remaining=player["bank"],
                )
            elapsed = int(time.time() - player["turn_start"])
            player["bank"] = max(0, player["bank"] - elapsed)
            player["turn_start"] = None
            remaining = player["bank"]

        log.info(f"EndTurn OK  {color} used {elapsed}s, {remaining}s remaining")
        return timer_pb2.EndTurnResponse(
            success=True,
            seconds_used=elapsed,
            seconds_remaining=remaining,
        )

# ── Entry point ────────────────────────────────────────────────────────────────

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    timer_pb2_grpc.add_TurnTimerServicer_to_server(TurnTimerServicer(), server)
    server.add_insecure_port("[::]:50053")
    server.start()
    log.info("Turn timer listening on port 50053  (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        server.stop(0)

if __name__ == "__main__":
    serve()
