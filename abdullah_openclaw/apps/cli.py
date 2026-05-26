import logging

from abdullah_openclaw.core.agent import Session
from abdullah_openclaw.agents.runtime import clear_skill_runtime_agent_cache, get_skill_runtime_for_agent
from abdullah_openclaw.core.events import (
    AssistantReplyFinished,
    ConfigReloaded,
    ReplStarted,
    SessionEnding,
    SlashCommandHandled,
    UserTurnReceived,
)
from abdullah_openclaw.core.event_bus import EventBus
from abdullah_openclaw.workspace.hot_reload_signals import reload_before_next_turn, take_reload_reason
from abdullah_openclaw.repl.observers import wire_default_observers
from abdullah_openclaw.routing.session_stems import effective_cli_agent_id
from abdullah_openclaw.routing.table import invalidate_routing_bindings_cache
from abdullah_openclaw.workspace.session_store import persistence_enabled, session_id_from_env, session_path
from abdullah_openclaw.workspace.skill_reload_watcher import (
    SkillEnvReloader,
    clear_pending_reload,
    skill_hot_reload_enabled,
)
from abdullah_openclaw.workspace.skill_runtime import PROJECT_ROOT
from abdullah_openclaw.core.skill_selection import skills_for_turn
from abdullah_openclaw.workspace.skills import build_system_prompt

from abdullah_openclaw.repl.slash import ReplState, cli_rehome, handle_slash, parse_slash_line

logger = logging.getLogger(__name__)


def main() -> None:
    watcher: SkillEnvReloader | None = None
    label = session_id_from_env()

    boot = ReplState(
        session=Session(system_prompt="."),
        sid="bootstrap",
        spath=session_path("bootstrap-placeholder"),
        initial="",
        cli_session_label=label,
        cli_route_override=None,
    )
    cli_rehome(boot, persist=persistence_enabled())
    state = boot

    bus = EventBus()
    wire_default_observers(bus, state)

    try:
        agent_id_open = effective_cli_agent_id(state.cli_route_override)
        rt_open = get_skill_runtime_for_agent(agent_id_open)
        bus.publish(
            ReplStarted(
                session_id=state.sid,
                skill_attach_mode=rt_open.mode,
                always_skill_ids=tuple(s.id for s in rt_open.always_skills),
                profile_tags=rt_open.active_profiles,
                agent_id=agent_id_open,
            )
        )

        logger.info(
            "skills: agent=%s mode=%s always=%s pool=%s profiles=%s",
            agent_id_open,
            rt_open.mode,
            [s.id for s in rt_open.always_skills],
            rt_open.pool_ids,
            list(rt_open.active_profiles),
        )

        if skill_hot_reload_enabled():
            watcher = SkillEnvReloader(project_root=PROJECT_ROOT)
            watcher.start()

        while True:
            if reload_before_next_turn.is_set():
                reload_before_next_turn.clear()
                reason = take_reload_reason()
                clear_skill_runtime_agent_cache()
                invalidate_routing_bindings_cache()
                cli_rehome(state, persist=persistence_enabled())
                rt_reload = get_skill_runtime_for_agent(effective_cli_agent_id(state.cli_route_override))
                bus.publish(ConfigReloaded(state.sid, reason))
                logger.info(
                    "skills: reload reason=%s agent=%s mode=%s always=%s pool=%s profiles=%s",
                    reason,
                    effective_cli_agent_id(state.cli_route_override),
                    rt_reload.mode,
                    [s.id for s in rt_reload.always_skills],
                    rt_reload.pool_ids,
                    list(rt_reload.active_profiles),
                )

            user_input = input("You: ").strip()

            if not user_input or user_input.lower() in {"quit", "exit", "q"}:
                bus.publish(SessionEnding(state.sid, "eof_or_quit"))
                break

            parsed = parse_slash_line(user_input)
            if parsed is not None:
                name, args = parsed
                outcome = handle_slash(name, args, state=state, persist=persistence_enabled())
                bus.publish(SlashCommandHandled(state.sid, name, tuple(args), outcome))
                if outcome == "break":
                    bus.publish(SessionEnding(state.sid, "slash_quit"))
                    break
                continue

            agent_id = effective_cli_agent_id(state.cli_route_override)
            rt = get_skill_runtime_for_agent(agent_id)

            deduped, auto_skill_ids = skills_for_turn(rt, user_input)
            bus.publish(UserTurnReceived(state.sid, user_input, auto_skill_ids))

            state.session.set_system_prompt(build_system_prompt(rt.foundation_prompt, deduped))
            reply = state.session.chat(user_input)
            print("Assistant:", reply, "\n")
            bus.publish(
                AssistantReplyFinished(state.sid, reply, len(state.session.messages)),
            )
    finally:
        if watcher is not None:
            watcher.stop()
        clear_pending_reload()


if __name__ == "__main__":
    main()
