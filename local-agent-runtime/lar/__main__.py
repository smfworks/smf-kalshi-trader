#!/usr/bin/env python3
"""
LAR — Local Agent Runtime CLI

Usage:
    lar --config config/local.yaml
    lar --agent-id gabriel --agent-name "Gabriel" run "What is the latest OpenClaw release?"
"""

import argparse
import asyncio
import sys
from pathlib import Path

import structlog

from lar.config import ConfigManager
from lar.identity import SessionIdentityValidator
from lar.agent import AgentLoop


def setup_logging(log_level: str, log_format: str):
    """Configure structured logging."""
    processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    
    if log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    
    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


async def main():
    parser = argparse.ArgumentParser(description="Local Agent Runtime")
    parser.add_argument("--config", "-c", type=str, help="Path to config YAML")
    parser.add_argument("--agent-id", type=str, help="Agent ID override")
    parser.add_argument("--agent-name", type=str, help="Agent name override")
    parser.add_argument("task", nargs="?", help="Task to execute")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    
    args = parser.parse_args()
    
    # Load configuration
    config_path = Path(args.config) if args.config else None
    config_manager = ConfigManager(config_path)
    config = config_manager.load()
    
    # Override from CLI
    if args.agent_id:
        config.agent_id = args.agent_id
    if args.agent_name:
        config.agent_name = args.agent_name
    
    # Setup logging
    setup_logging(config.log_level, config.log_format)
    logger = structlog.get_logger("lar.cli")
    logger.info("lar_startup", agent_id=config.agent_id, config=str(config_manager.config_path))
    
    # Initialize identity validator
    identity = SessionIdentityValidator(
        expected_agent_id=config.agent_id,
        expected_session_key=config.session_key,
        max_payload_age_seconds=config.identity.max_payload_age_seconds,
        hmac_secret=config.identity.hmac_secret,
        strict_session_key=config.identity.strict_session_key,
    )
    
    # Initialize agent loop
    agent = AgentLoop(config, identity)
    await agent.setup()
    
    try:
        if args.task:
            # Single task mode
            logger.info("executing_task", task=args.task[:100])
            result = await agent.run_cycle(args.task)
            print(result)
        elif args.interactive:
            # Interactive mode
            print(f"🦞 LAR — Local Agent Runtime")
            print(f"Agent: {config.agent_name} ({config.agent_id})")
            print("Type 'exit' or 'quit' to stop.\n")
            
            while True:
                try:
                    task = input("\033[1;32m>>>\033[0m ")
                    if task.lower() in ("exit", "quit", "q"):
                        break
                    
                    result = await agent.run_cycle(task)
                    print(f"\n{result}\n")
                    
                except KeyboardInterrupt:
                    break
                except EOFError:
                    break
        else:
            print("Usage: lar --config config.yaml run 'Your task here'")
            print("       lar --config config.yaml --interactive")
            sys.exit(1)
    
    finally:
        await agent.shutdown()
        logger.info("lar_shutdown", agent_id=config.agent_id)


if __name__ == "__main__":
    asyncio.run(main())
