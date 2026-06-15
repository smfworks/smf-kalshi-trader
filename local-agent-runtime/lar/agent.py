import asyncio
import json
import time
from typing import Optional, Any, TYPE_CHECKING
import structlog

from lar.config import ConfigManager, RuntimeConfig
from lar.identity import SessionIdentityValidator, ValidationResult
from lar.llm import LLMBackend, OllamaBackend, FallbackBackend, LLMResponse
from lar.tools import ToolRegistry

if TYPE_CHECKING:
    from lar.observatory import Observatory, StepEvent

logger = structlog.get_logger("lar.agent")


class AgentLoop:
    """
    Core autonomous agent execution loop.
    
    Implements the OATA cycle: Observe → Think → Act → (optional Learn)
    """
    
    def __init__(self, config: RuntimeConfig, identity: SessionIdentityValidator,
                 observatory: Optional["Observatory"] = None):
        self.config = config
        self.identity = identity
        self.tool_registry = ToolRegistry()
        self.llm: Optional[LLMBackend] = None
        self._running = False
        self._message_history: list[dict] = []
        self.observatory = observatory  # optional live visualizer

        logger.info("agent_loop_initialized", agent_id=config.agent_id)
    
    async def setup(self) -> None:
        """Initialize LLM backend and tools."""
        # Initialize primary LLM backend
        primary = OllamaBackend(
            model=self.config.model.model,
            base_url=self.config.model.base_url,
            timeout=self.config.model.timeout,
        )
        
        # Set up fallback backends if configured
        if self.config.model.fallbacks:
            fallbacks = [
                OllamaBackend(model=m, base_url=self.config.model.base_url)
                for m in self.config.model.fallbacks
            ]
            self.llm = FallbackBackend([primary] + fallbacks, self.config.model.fallbacks)
        else:
            self.llm = primary
        
        # Register built-in tools from config
        tool_config = {}
        for tc in self.config.tools:
            if tc.name == "exec":
                tool_config["exec"] = tc.config
            elif tc.name in ("file_read", "file_write"):
                tool_config.setdefault("file", {}).update(tc.config)
        
        try:
            from lar.tools.builtin import register_builtin_tools
            register_builtin_tools(self.tool_registry, tool_config)
            logger.info("builtin_tools_registered", count=len(self.tool_registry.get_tool_names()))
        except ImportError as e:
            logger.warning("builtin_tools_import_failed", error=str(e))
        
        # Health check
        health = await self.llm.health_check()
        logger.info("llm_health_check", status=health)
        
        logger.info("agent_setup_complete", tools=self.tool_registry.get_tool_names())
    
    async def run_cycle(
        self,
        task: str,
        payload: Optional[dict] = None,
        checkpoint_store=None,
        task_id: str = "default",
    ) -> str:
        """
        Execute one full agent cycle with optional checkpoint/resume.

        If a previous incomplete checkpoint exists for task_id, resume from
        that checkpoint. Otherwise, start fresh and checkpoint at every step
        boundary.

        Args:
            task: The user's task or message
            payload: Optional incoming payload for identity validation
            checkpoint_store: Optional CheckpointStore for persistence
            task_id: Unique identifier for this task (for resume)

        Returns:
            The final response string
        """
        # ---- CHECKPOINT: Resume from previous if available ----
        if checkpoint_store:
            from lar.checkpoint import CheckpointStore, AgentState, Phase
            latest = await checkpoint_store.latest_for_task(task_id)
            if latest and not latest.is_complete:
                logger.info(
                    "resuming_from_checkpoint",
                    task_id=task_id,
                    step=latest.step_number,
                    phase=latest.phase.value,
                )
                self._message_history = latest.messages
                step_number = latest.step_number
                # Continue from where we left off
            else:
                step_number = 0
                self._message_history.append({"role": "user", "content": task})
        else:
            step_number = 0
            self._message_history.append({"role": "user", "content": task})

        # Step 1: OBSERVE — Validate identity if payload provided
        if payload:
            valid, error = self.identity.validate(payload)
            if not valid:
                logger.warning(
                    "payload_rejected",
                    reason=error.result.name if error else "unknown",
                )
                if self.observatory:
                    self.observatory.record_error(f"identity rejected: {error.reason}")
                return f"Payload rejected: {error.reason if error else 'unknown error'}"

        # Step 2: THINK — Query LLM with context and available tools
        logger.info("thinking", task_preview=task[:100])
        t0 = time.time()
        tools = self.tool_registry.list_tools()

        try:
            response = await self.llm.chat(
                self._message_history, tools=tools if tools else None
            )
        except Exception as e:
            logger.error("llm_chat_failed", error=str(e))
            if self.observatory:
                self.observatory.record_error(f"llm_chat_failed: {e}")
            return f"Error: LLM backend failed — {str(e)}"
        think_ms = (time.time() - t0) * 1000
        if self.observatory:
            from lar.observatory import StepEvent
            self.observatory.record_step(StepEvent(
                timestamp=time.time(), phase="think",
                step_number=step_number + 1, duration_ms=think_ms,
                detail=f"chat → {getattr(response, 'model_used', 'unknown')}",
                success=True,
            ))

        step_number += 1

        # Step 3: ACT — Execute tool calls if present
        if response.tool_calls:
            logger.info("tool_calls_detected", count=len(response.tool_calls))

            tool_results = []
            for tool_call in response.tool_calls:
                func = tool_call.get("function", {})
                name = func.get("name", "")
                arguments = func.get("arguments", {})

                if name in self.tool_registry:
                    t_tool = time.time()
                    result = await self.tool_registry.execute(name, **arguments)
                    tool_ms = (time.time() - t_tool) * 1000
                    if self.observatory:
                        from lar.observatory import StepEvent
                        self.observatory.record_step(StepEvent(
                            timestamp=time.time(), phase="act",
                            step_number=step_number, duration_ms=tool_ms,
                            tool=name,
                            tool_input=arguments,
                            tool_output_preview=str(result.to_dict())[:200],
                            success=True,
                        ))
                    tool_results.append({"tool": name, "result": result.to_dict()})
                else:
                    if self.observatory:
                        from lar.observatory import StepEvent
                        self.observatory.record_step(StepEvent(
                            timestamp=time.time(), phase="act",
                            step_number=step_number, duration_ms=0,
                            tool=name, tool_input=arguments,
                            success=False,
                            detail=f"tool '{name}' not found",
                        ))
                    tool_results.append({
                        "tool": name,
                        "error": f"Tool '{name}' not found in registry",
                    })

            # Add tool results to message history
            self._message_history.append({
                "role": "assistant",
                "content": response.content,
                "tool_calls": response.tool_calls,
            })

            for result in tool_results:
                self._message_history.append({
                    "role": "tool",
                    "content": json.dumps(result),
                })

            # CHECKPOINT: After tool execution
            if checkpoint_store:
                from lar.checkpoint import AgentState, Phase
                state = AgentState(
                    task_id=task_id,
                    step_number=step_number,
                    phase=Phase.ACT,
                    context={},  # placeholder for real context
                    messages=self._message_history.copy(),
                    tool_calls=[tc for tc in response.tool_calls],
                    tool_results=[str(r) for r in tool_results],
                    memory_snapshot={},  # memory.dict() if memory exists
                    iteration=step_number,
                    model_used=getattr(response, "model_used", "unknown") or "unknown",
                    tools_available=[t.name for t in self.tool_registry.registry.values()],
                    checkpoint_reason="step_boundary",
                )
                cp_id = await checkpoint_store.save(state)
                logger.debug("checkpoint_saved", checkpoint_id=cp_id)
                if self.observatory:
                    self.observatory.record_checkpoint()

            # Step 4: THINK again with tool results
            logger.info("rethinking_with_tool_results", results=len(tool_results))
            try:
                final_response = await self.llm.chat(self._message_history)
                response = final_response
            except Exception as e:
                logger.error("llm_rethink_failed", error=str(e))
                if self.observatory:
                    self.observatory.record_error(f"llm_rethink_failed: {e}")
                return f"Error: LLM failed after tool execution — {str(e)}"

        # Step 5: RESPOND — Return final output
        self._message_history.append({"role": "assistant", "content": response.content})
        if self.observatory:
            from lar.observatory import StepEvent
            self.observatory.record_step(StepEvent(
                timestamp=time.time(), phase="respond",
                step_number=step_number, duration_ms=0,
                detail=response.content[:200], success=True,
            ))

        # FINAL CHECKPOINT: Mark complete
        if checkpoint_store:
            from lar.checkpoint import AgentState, Phase
            state = AgentState(
                task_id=task_id,
                step_number=step_number,
                phase=Phase.RESPOND,
                context={},
                messages=self._message_history.copy(),
                tool_calls=[],  # Already recorded
                tool_results=[],  # Already recorded
                memory_snapshot={},
                is_complete=True,
                iteration=step_number,
                model_used=getattr(response, "model_used", "unknown") or "unknown",
                tools_available=[t.name for t in self.tool_registry.registry.values()],
                checkpoint_reason="step_boundary",
            )
            cp_id = await checkpoint_store.save(state)
            logger.info("cycle_complete_checkpointed", checkpoint_id=cp_id)
            if self.observatory:
                self.observatory.record_checkpoint()

        logger.info(
            "cycle_complete",
            response_preview=response.content[:200],
            history_length=len(self._message_history),
        )

        return response.content
    
    async def shutdown(self) -> None:
        """Clean up resources."""
        if self.llm:
            await self.llm.close()
        logger.info("agent_loop_shutdown")
