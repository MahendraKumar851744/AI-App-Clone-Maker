from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


JsonObject = dict[str, Any]

ACTION_PATTERN = re.compile(
    r'- \[(element_\d+)\] ([a-z_]+) \| "([^"]*)"',
)


def utc_now() -> str:

    return datetime.now(timezone.utc).isoformat()


class ExplorationGraphStore:
    """Evidence-backed screen dossiers, transitions, branches, and app memory."""

    def __init__(
        self,
        *,
        package_id: str,
        objective: str,
        output_directory: Path | None = None,
    ) -> None:

        self.output_directory = output_directory
        now = utc_now()

        self.graph: JsonObject = {
            "contract": "workflow.screen_knowledge_graph",
            "schema_version": 2,
            "package_id": package_id,
            "objective": objective,
            "created_at": now,
            "updated_at": now,
            "status": "running",
            "current_node_id": None,
            "active_branch_id": None,
            "nodes": [],
            "visits": [],
            "edges": [],
            "branches": [],
            "frontier": [],
            "iterations": 0,
        }

        self.knowledge: JsonObject = {
            "contract": "workflow.application_knowledge",
            "schema_version": 2,
            "package_id": package_id,
            "app_summary": "",
            "features": [],
            "domain_objects": [],
            "global_ui_patterns": [],
            "navigation_model": [],
            "facts": [],
            "hypotheses": [],
            "workflows": [],
            "failures": [],
            "open_questions": [],
            "notes": [],
            "updated_at": now,
        }

        self.persist()

    def observe_screen(
        self,
        *,
        screen_id: str,
        fingerprint: str | None,
        context_text: str,
        package: str | None = None,
        activity: str | None = None,
        screenshot: str | None = None,
        track_active_branch: bool = True,
    ) -> JsonObject:

        identity = fingerprint or screen_id
        node = self._node_by_identity(identity)
        now = utc_now()

        if node is None:
            node = self._new_node(
                identity=identity,
                fingerprint=fingerprint,
                screen_id=screen_id,
                package=package,
                activity=activity,
                now=now,
            )
            self.graph["nodes"].append(node)

        else:
            node["last_seen_at"] = now
            node["package"] = package or node.get("package")
            node["activity"] = activity or node.get("activity")

            if fingerprint and fingerprint not in node["fingerprints"]:
                node["fingerprints"].append(fingerprint)

            if screen_id not in node["screen_ids"]:
                node["screen_ids"].append(screen_id)

        if not self._visit_by_screen_id(screen_id):
            visit = {
                "visit_id": f"visit_{len(self.graph['visits']) + 1}",
                "node_id": node["node_id"],
                "screen_id": screen_id,
                "fingerprint": fingerprint,
                "package": package,
                "activity": activity,
                "screenshot": screenshot,
                "state_id": None,
                "context_excerpt": context_text[:4000],
                "observed_at": now,
            }
            self.graph["visits"].append(visit)
            node["visit_ids"].append(visit["visit_id"])
            node["visit_count"] = len(node["visit_ids"])

        self._merge_available_actions(node, context_text)
        self._ensure_root_branch(node["node_id"])

        if track_active_branch:
            self._append_node_to_active_branch(node["node_id"])

        self.graph["current_node_id"] = node["node_id"]
        self._refresh_frontier()
        self._touch()
        self.persist()

        return node

    def apply_screen_analysis(
        self,
        node_id: str,
        screen_id: str,
        analysis: JsonObject,
        *,
        evidence: JsonObject | None = None,
    ) -> JsonObject:

        node = self.node(node_id)
        dossier = node["dossier"]
        now = utc_now()

        semantic_name = self._clean_string(analysis.get("semantic_name"))
        screen_type = self._clean_string(analysis.get("screen_type"))
        functional_purpose = self._clean_string(
            analysis.get("functional_purpose")
        )
        layout_summary = self._clean_string(analysis.get("layout_summary"))

        if semantic_name:
            node["semantic_name"] = semantic_name

        if screen_type:
            node["screen_type"] = screen_type

        if functional_purpose:
            dossier["functional"]["purpose"] = functional_purpose
            node["purpose"] = functional_purpose

        if layout_summary:
            dossier["ui"]["layout_summary"] = layout_summary

        self._extend_unique(
            dossier["functional"]["user_goals"],
            analysis.get("user_goals", []),
        )
        self._extend_unique(
            dossier["functional"]["capabilities"],
            analysis.get("capabilities", []),
        )
        self._extend_unique(
            dossier["functional"]["entry_conditions"],
            analysis.get("entry_conditions", []),
        )
        self._extend_unique(
            dossier["functional"]["exit_paths"],
            analysis.get("exit_paths", []),
        )
        self._merge_named_objects(
            dossier["ui"]["regions"],
            analysis.get("regions", []),
            keys=("name", "region"),
        )
        self._extend_unique(
            dossier["ui"]["visible_content"],
            analysis.get("visible_content", []),
        )
        self._merge_controls(
            node,
            analysis.get("controls", []),
        )

        state = self._merge_state(
            node,
            screen_id,
            analysis,
        )
        self._extend_unique(dossier["facts"], analysis.get("facts", []))
        self._extend_unique(
            dossier["hypotheses"],
            analysis.get("hypotheses", []),
        )
        self._extend_unique(
            dossier["open_questions"],
            analysis.get("open_questions", []),
        )
        self._merge_workflows(
            dossier["workflows"],
            analysis.get("workflow_updates", []),
        )

        confidence = analysis.get("confidence")

        if isinstance(confidence, (int, float)) and not isinstance(
            confidence,
            bool,
        ):
            dossier["confidence"] = max(0.0, min(1.0, float(confidence)))

        summary = self._screen_summary(node)
        node["summary"] = summary
        dossier["summary"] = summary

        evidence_record = {
            "screen_id": screen_id,
            "visit_id": (
                self._visit_by_screen_id(screen_id) or {}
            ).get("visit_id"),
            "state_id": state.get("state_id"),
            "captured_at": now,
            **dict(evidence or {}),
        }
        dossier["evidence"].append(evidence_record)

        revision = {
            "revision": len(dossier["revision_history"]) + 1,
            "screen_id": screen_id,
            "summary": summary,
            "semantic_name": node.get("semantic_name"),
            "state_id": state.get("state_id"),
            "facts_added": list(analysis.get("facts", [])),
            "hypotheses_added": list(analysis.get("hypotheses", [])),
            "open_questions": list(analysis.get("open_questions", [])),
            "created_at": now,
        }
        dossier["revision_history"].append(revision)
        dossier["updated_at"] = now

        visit = self._visit_by_screen_id(screen_id)

        if visit is not None:
            visit["state_id"] = state.get("state_id")

        self._apply_application_update(
            analysis.get("app_knowledge_update", {}),
        )
        self._enrich_incoming_transition(
            node_id,
            screen_id,
            analysis.get("transition_understanding"),
        )
        self._update_active_branch_from_analysis(node_id, analysis)
        self._refresh_frontier()
        self._touch()
        self.persist()

        return node

    def apply_agent_understanding(
        self,
        node_id: str,
        decision: JsonObject,
    ) -> None:
        """Compatibility path for older callers using one combined LLM response."""

        node = self.node(node_id)
        screen_id = (
            node.get("screen_ids", [])[-1]
            if node.get("screen_ids")
            else f"legacy_{node_id}"
        )
        controls = []

        for control in decision.get("controls", []):

            if not isinstance(control, dict):
                continue

            controls.append(
                {
                    **control,
                    "control_type": control.get("control_type") or "unknown",
                    "function": (
                        control.get("function")
                        or control.get("likely_function")
                        or ""
                    ),
                    "interactions": control.get("interactions") or ["tap"],
                }
            )

        update = decision.get("knowledge_update", {})
        analysis = {
            "semantic_name": decision.get("screen_summary") or node_id,
            "screen_type": "unknown",
            "functional_purpose": decision.get("screen_purpose") or "",
            "user_goals": [],
            "capabilities": [],
            "entry_conditions": [],
            "exit_paths": [],
            "layout_summary": decision.get("screen_summary") or "",
            "regions": [],
            "visible_content": decision.get("observations", []),
            "controls": controls,
            "state_name": "observed",
            "state_description": decision.get("screen_summary") or "",
            "facts": update.get("facts", []),
            "hypotheses": update.get("hypotheses", []),
            "open_questions": update.get("open_questions", []),
            "workflow_updates": update.get("workflows", []),
            "branch_discoveries": decision.get("observations", []),
            "transition_understanding": None,
            "app_knowledge_update": update,
            "confidence": decision.get("confidence", 0.5),
        }
        self.apply_screen_analysis(node_id, screen_id, analysis)
        self.apply_planning_decision(node_id, decision)

    def apply_planning_decision(
        self,
        node_id: str,
        decision: JsonObject,
    ) -> None:

        node = self.node(node_id)

        for blocked in decision.get("blocked_actions", []):

            if not isinstance(blocked, dict):
                continue

            action = self._action(
                node,
                str(blocked.get("action_key") or ""),
            )

            if action is not None:
                action["status"] = "blocked"
                action["blocked_reason"] = str(
                    blocked.get("reason") or ""
                )

        branch = self.active_branch()

        if branch is not None:
            branch["current_goal"] = str(
                decision.get("exploration_goal") or ""
            )
            branch["return_plan"] = str(
                decision.get("return_plan") or ""
            )
            self._extend_unique(
                branch["open_questions"],
                decision.get("remaining_areas", []),
            )
            self._extend_unique(
                branch["recent_open_questions"],
                decision.get("remaining_areas", []),
            )

        self._refresh_frontier()
        self._touch()
        self.persist()

    def record_transition(
        self,
        *,
        iteration: int,
        from_node_id: str,
        action_request: JsonObject,
        action_result: JsonObject,
        expectation: JsonObject | None = None,
    ) -> JsonObject:

        after = action_result.get("after", {})
        health = action_result.get("app_health", {})
        screen_id = str(after.get("screen_id") or f"unknown_{iteration}")
        target = self.observe_screen(
            screen_id=screen_id,
            fingerprint=(
                str(after["fingerprint"])
                if after.get("fingerprint")
                else None
            ),
            context_text="",
            package=(
                str(health["current_package"])
                if health.get("current_package")
                else None
            ),
            track_active_branch=False,
        )
        source = self.node(from_node_id)
        action_key = self.action_key(action_request)
        source_action = self._action(source, action_key)

        if source_action is None:
            source_action = {
                "action_key": action_key,
                "action": action_request.get("action"),
                "element_id": action_request.get(
                    "target",
                    {},
                ).get("element_id"),
                "label": "",
                "status": "explored",
                "attempts": 0,
                "outcomes": [],
            }
            source["actions"].append(source_action)

        source_action["status"] = "explored"
        source_action["attempts"] = int(
            source_action.get("attempts", 0)
        ) + 1

        outcome = {
            "classification": action_result.get("classification"),
            "app_health": health.get("status"),
            "to_node_id": target["node_id"],
            "screen_id": screen_id,
        }
        source_action.setdefault("outcomes", []).append(outcome)

        branch = self._select_branch_for_transition(
            from_node_id,
            target["node_id"],
            action_key,
            str(action_request.get("action") or ""),
        )
        edge = {
            "edge_id": f"edge_{len(self.graph['edges']) + 1}",
            "iteration": iteration,
            "branch_id": branch["branch_id"],
            "from_node_id": from_node_id,
            "to_node_id": target["node_id"],
            "action_key": action_key,
            "trigger": {
                "action": action_request.get("action"),
                "element_id": action_request.get(
                    "target",
                    {},
                ).get("element_id"),
                "label": source_action.get("label"),
                "parameters": action_request.get("parameters", {}),
            },
            "preconditions": list(
                (expectation or {}).get("preconditions", [])
            ),
            "expected_effect": str(
                (expectation or {}).get("expected_result") or ""
            ),
            "observed_effect": action_result.get("effect"),
            "functional_effect": "",
            "workflow_role": "",
            "side_effects": [],
            "reversible": None,
            "return_action": None,
            "classification": action_result.get("classification"),
            "app_health": health,
            "errors": action_result.get("errors", []),
            "transition_id": action_result.get("transition_id"),
            "evidence": {
                "before_node_id": from_node_id,
                "after_screen_id": screen_id,
                "action_id": action_result.get("action_id"),
            },
            "created_at": utc_now(),
        }
        self.graph["edges"].append(edge)
        branch["edge_ids"].append(edge["edge_id"])
        self._append_unique(branch["node_ids"], target["node_id"])
        branch["path"].append(target["node_id"])
        branch["changes_since_summary"] = int(
            branch.get("changes_since_summary", 0)
        ) + 1
        self.graph["active_branch_id"] = branch["branch_id"]
        self.graph["current_node_id"] = target["node_id"]
        self.graph["iterations"] = iteration

        unhealthy = str(health.get("status") or "")

        if unhealthy in {"crashed", "anr", "process_terminated"}:
            failure = (
                f"{action_key} from {source['node_id']} caused {unhealthy}."
            )
            self._extend_unique(self.knowledge["failures"], [failure])
            self._extend_unique(branch["failures"], [failure])

        self._refresh_frontier()
        self._touch()
        self.persist()

        return edge

    def active_branch(self) -> JsonObject | None:

        active_id = self.graph.get("active_branch_id")

        return next(
            (
                branch
                for branch in self.graph["branches"]
                if branch.get("branch_id") == active_id
            ),
            None,
        )

    def branch_context(self, current_node_id: str) -> JsonObject:

        branch = self.active_branch() or {}
        has_summary = bool(branch.get("summary"))

        return {
            "branch_id": branch.get("branch_id"),
            "parent_branch_id": branch.get("parent_branch_id"),
            "ancestor_branches": self._ancestor_branch_context(branch),
            "root_node_id": branch.get("root_node_id"),
            "entry_action_key": branch.get("entry_action_key"),
            "goal": branch.get("current_goal"),
            "path": branch.get("path", []),
            "summary": branch.get("summary", ""),
            "recent_discoveries": (
                branch.get("recent_discoveries", [])
                if has_summary
                else branch.get("discoveries", [])
            ),
            "confirmed_workflows": (
                []
                if has_summary
                else branch.get("confirmed_workflows", [])
            ),
            "failures": branch.get("failures", []),
            "open_questions": (
                branch.get("recent_open_questions", [])
                if has_summary
                else branch.get("open_questions", [])
            ),
            "unresolved_frontier": [
                item
                for item in self.graph["frontier"]
                if item.get("node_id") in branch.get("node_ids", [])
            ],
            "return_plan": branch.get("return_plan", ""),
            "current_node_id": current_node_id,
        }

    def screen_analysis_context(self, node_id: str) -> JsonObject:

        node = self.node(node_id)
        incoming = [
            edge
            for edge in self.graph["edges"]
            if edge.get("to_node_id") == node_id
        ]

        return {
            "existing_screen_dossier": node,
            "incoming_transition": incoming[-1] if incoming else None,
            "active_branch": self.branch_context(node_id),
            "application_overview": self._application_overview(),
        }

    def agent_context(self, current_node_id: str) -> JsonObject:

        current = self.node(current_node_id)
        neighbor_ids: list[str] = []

        for edge in self.graph["edges"]:

            if edge.get("from_node_id") == current_node_id:
                self._append_unique(neighbor_ids, edge.get("to_node_id"))

            if edge.get("to_node_id") == current_node_id:
                self._append_unique(neighbor_ids, edge.get("from_node_id"))

        neighbors = [
            self._compact_node(self.node(node_id))
            for node_id in neighbor_ids
            if node_id and node_id != current_node_id
        ]
        context = {
            "current_screen": current,
            "active_branch": self.branch_context(current_node_id),
            "local_neighborhood": neighbors,
            "relevant_frontier": self._relevant_frontier(
                current_node_id,
                neighbor_ids,
            ),
            "application_overview": self._application_overview(),
            "coverage": self.coverage(),
        }
        context["context_metrics"] = {
            "estimated_characters": len(
                json.dumps(context, ensure_ascii=False)
            ),
            "nodes_included": 1 + len(neighbors),
            "total_nodes": len(self.graph["nodes"]),
            "full_graph_omitted": True,
        }

        return context

    def needs_branch_summary(
        self,
        current_node_id: str,
        *,
        context_budget_characters: int = 55_000,
        summarize_after_changes: int = 6,
    ) -> bool:

        branch = self.active_branch()

        if branch is None:
            return False

        context_size = len(
            json.dumps(
                self.agent_context(current_node_id),
                ensure_ascii=False,
            )
        )

        return (
            int(branch.get("changes_since_summary", 0))
            >= summarize_after_changes
            or context_size > context_budget_characters
        )

    def branch_summary_input(self, current_node_id: str) -> JsonObject:

        branch = self.active_branch() or {}
        node_ids = branch.get("node_ids", [])

        return {
            "branch": branch,
            "screen_dossiers": [
                self.node(node_id)
                for node_id in node_ids
                if self._has_node(node_id)
            ],
            "transitions": [
                edge
                for edge in self.graph["edges"]
                if edge.get("branch_id") == branch.get("branch_id")
            ],
            "current_node_id": current_node_id,
            "application_overview": self._application_overview(),
        }

    def apply_branch_summary(
        self,
        summary: JsonObject,
    ) -> JsonObject:

        branch = self.active_branch()

        if branch is None:
            raise RuntimeError("There is no active branch to summarize.")

        branch["summary"] = str(summary.get("branch_summary") or "")
        self._extend_unique(
            branch["discoveries"],
            summary.get("key_discoveries", []),
        )
        self._merge_workflows(
            branch["confirmed_workflows"],
            summary.get("confirmed_workflows", []),
        )
        branch["open_questions"] = self._unique_values(
            summary.get("open_questions", []),
        )
        branch["unresolved_frontier_summary"] = self._unique_values(
            summary.get("unresolved_frontier", []),
        )
        branch["changes_since_summary"] = 0
        branch["recent_discoveries"] = []
        branch["recent_open_questions"] = []
        branch["summary_revision"] = int(
            branch.get("summary_revision", 0)
        ) + 1
        branch["summarized_at"] = utc_now()

        app_patch = summary.get("application_knowledge_patch", {})

        if isinstance(app_patch, dict):
            self._apply_application_update(app_patch)

        self._touch()
        self.persist()

        return branch

    def finish(self, status: str) -> None:

        self.graph["status"] = status

        for branch in self.graph["branches"]:

            if branch.get("status") == "active":
                branch["status"] = (
                    "completed"
                    if not self._branch_frontier(branch)
                    else "incomplete"
                )

        self._touch()
        self.persist()

    def node(self, node_id: str) -> JsonObject:

        for node in self.graph["nodes"]:

            if node.get("node_id") == node_id:
                return node

        raise KeyError(f"Unknown graph node '{node_id}'.")

    def action_key(self, action_request: JsonObject) -> str:

        element_id = action_request.get("target", {}).get("element_id")

        return (
            f"{action_request.get('action')}:{element_id}"
            if element_id
            else str(action_request.get("action"))
        )

    def unresolved_actions(
        self,
        node_id: str,
        proposed_blocked: list[JsonObject] | None = None,
    ) -> list[JsonObject]:

        blocked = {
            str(item.get("action_key"))
            for item in proposed_blocked or []
            if isinstance(item, dict)
        }

        return [
            action
            for action in self.node(node_id)["actions"]
            if action.get("status") == "unexplored"
            and action.get("action_key") not in blocked
        ]

    def total_unresolved_actions(
        self,
        current_node_id: str,
        proposed_blocked: list[JsonObject] | None = None,
    ) -> list[JsonObject]:

        unresolved: list[JsonObject] = []

        for node in self.graph["nodes"]:
            actions = (
                self.unresolved_actions(node["node_id"], proposed_blocked)
                if node["node_id"] == current_node_id
                else [
                    action
                    for action in node.get("actions", [])
                    if action.get("status") == "unexplored"
                ]
            )
            unresolved.extend(
                {
                    **action,
                    "node_id": node["node_id"],
                }
                for action in actions
            )

        return unresolved

    def known_action(
        self,
        node_id: str,
        action_request: JsonObject,
    ) -> bool:

        return self._action(
            self.node(node_id),
            self.action_key(action_request),
        ) is not None

    def is_explored(
        self,
        node_id: str,
        action_request: JsonObject,
    ) -> bool:

        action = self._action(
            self.node(node_id),
            self.action_key(action_request),
        )

        return bool(action and action.get("status") == "explored")

    def coverage(self) -> JsonObject:

        actions = [
            action
            for node in self.graph["nodes"]
            for action in node.get("actions", [])
        ]
        controls = [
            control
            for node in self.graph["nodes"]
            for control in node.get("dossier", {}).get("controls", [])
        ]
        states = [
            state
            for node in self.graph["nodes"]
            for state in node.get("dossier", {}).get("states", [])
        ]
        explored = sum(
            action.get("status") == "explored"
            for action in actions
        )
        blocked = sum(
            action.get("status") == "blocked"
            for action in actions
        )

        return {
            "screens_discovered": len(self.graph["nodes"]),
            "screen_visits": len(self.graph["visits"]),
            "screen_states": len(states),
            "controls_understood": len(controls),
            "transitions_recorded": len(self.graph["edges"]),
            "branches_discovered": len(self.graph["branches"]),
            "actions_discovered": len(actions),
            "actions_explored": explored,
            "actions_blocked": blocked,
            "actions_remaining": max(
                0,
                len(actions) - explored - blocked,
            ),
        }

    def viewer_snapshot(self) -> JsonObject:

        return {
            "graph": {
                "contract": self.graph["contract"],
                "schema_version": self.graph["schema_version"],
                "status": self.graph["status"],
                "current_node_id": self.graph["current_node_id"],
                "active_branch_id": self.graph["active_branch_id"],
                "coverage": self.coverage(),
                "nodes": self.graph["nodes"],
                "visits": self.graph["visits"],
                "edges": self.graph["edges"],
                "branches": self.graph["branches"],
                "frontier": self.graph["frontier"],
            },
            "knowledge_box": self.knowledge,
        }

    def persist(self) -> None:

        if self.output_directory is None:
            return

        self.output_directory.mkdir(parents=True, exist_ok=True)
        self._write(
            self.output_directory / "exploration-graph.json",
            self.graph,
        )
        self._write(
            self.output_directory / "knowledge-box.json",
            self.knowledge,
        )

    def _new_node(
        self,
        *,
        identity: str,
        fingerprint: str | None,
        screen_id: str,
        package: str | None,
        activity: str | None,
        now: str,
    ) -> JsonObject:

        return {
            "node_id": f"node_{len(self.graph['nodes']) + 1}",
            "identity": identity,
            "fingerprints": [fingerprint] if fingerprint else [],
            "fingerprint": fingerprint,
            "screen_ids": [screen_id],
            "visit_ids": [],
            "package": package,
            "activity": activity,
            "semantic_name": "",
            "screen_type": "unknown",
            "summary": "",
            "purpose": "",
            "actions": [],
            "visit_count": 0,
            "first_seen_at": now,
            "last_seen_at": now,
            "dossier": {
                "summary": "",
                "functional": {
                    "purpose": "",
                    "user_goals": [],
                    "capabilities": [],
                    "entry_conditions": [],
                    "exit_paths": [],
                },
                "ui": {
                    "layout_summary": "",
                    "regions": [],
                    "visible_content": [],
                },
                "controls": [],
                "states": [],
                "workflows": [],
                "facts": [],
                "hypotheses": [],
                "open_questions": [],
                "evidence": [],
                "revision_history": [],
                "confidence": 0.0,
                "updated_at": now,
            },
        }

    def _ensure_root_branch(self, node_id: str) -> None:

        if self.graph["branches"]:
            return

        branch = {
            "branch_id": "branch_1",
            "parent_branch_id": None,
            "root_node_id": node_id,
            "entry_action_key": None,
            "status": "active",
            "current_goal": "Understand the application entry screen",
            "node_ids": [node_id],
            "edge_ids": [],
            "path": [node_id],
            "summary": "",
            "summary_revision": 0,
            "discoveries": [],
            "recent_discoveries": [],
            "confirmed_workflows": [],
            "failures": [],
            "open_questions": [],
            "recent_open_questions": [],
            "unresolved_frontier_summary": [],
            "return_plan": "",
            "changes_since_summary": 0,
            "created_at": utc_now(),
            "summarized_at": None,
        }
        self.graph["branches"].append(branch)
        self.graph["active_branch_id"] = branch["branch_id"]

    def _select_branch_for_transition(
        self,
        from_node_id: str,
        to_node_id: str,
        action_key: str,
        action_name: str,
    ) -> JsonObject:

        active = self.active_branch()

        if action_name in {"restart_app", "return_to_start"}:

            if active:
                active["status"] = "paused"

            root = self.graph["branches"][0]
            root["status"] = "active"
            return root

        if action_name in {"back", "recover", "activate_app"} and active:
            parent_id = active.get("parent_branch_id")

            if parent_id:
                active["status"] = (
                    "completed"
                    if not self._branch_frontier(active)
                    else "paused"
                )
                parent = self._branch(parent_id)

                if parent is not None:
                    parent["status"] = "active"
                    return parent

            return active

        existing = next(
            (
                branch
                for branch in self.graph["branches"]
                if branch.get("root_node_id") == from_node_id
                and branch.get("entry_action_key") == action_key
            ),
            None,
        )

        if existing is not None:
            existing["status"] = "active"
            return existing

        branch = {
            "branch_id": f"branch_{len(self.graph['branches']) + 1}",
            "parent_branch_id": (
                active.get("branch_id")
                if active
                else None
            ),
            "root_node_id": from_node_id,
            "entry_action_key": action_key,
            "status": "active",
            "current_goal": "",
            "node_ids": [from_node_id, to_node_id],
            "edge_ids": [],
            "path": [from_node_id],
            "summary": "",
            "summary_revision": 0,
            "discoveries": [],
            "recent_discoveries": [],
            "confirmed_workflows": [],
            "failures": [],
            "open_questions": [],
            "recent_open_questions": [],
            "unresolved_frontier_summary": [],
            "return_plan": "",
            "changes_since_summary": 0,
            "created_at": utc_now(),
            "summarized_at": None,
        }

        if active:
            active["status"] = "paused"

        self.graph["branches"].append(branch)

        return branch

    def _append_node_to_active_branch(self, node_id: str) -> None:

        branch = self.active_branch()

        if branch is None:
            return

        self._append_unique(branch["node_ids"], node_id)

        if not branch["path"] or branch["path"][-1] != node_id:
            branch["path"].append(node_id)

    def _update_active_branch_from_analysis(
        self,
        node_id: str,
        analysis: JsonObject,
    ) -> None:

        branch = self.active_branch()

        if branch is None:
            return

        self._append_unique(branch["node_ids"], node_id)
        self._extend_unique(
            branch["discoveries"],
            analysis.get("branch_discoveries", []),
        )
        self._extend_unique(
            branch["recent_discoveries"],
            analysis.get("branch_discoveries", []),
        )
        self._extend_unique(
            branch["open_questions"],
            analysis.get("open_questions", []),
        )
        self._extend_unique(
            branch["recent_open_questions"],
            analysis.get("open_questions", []),
        )
        self._merge_workflows(
            branch["confirmed_workflows"],
            analysis.get("workflow_updates", []),
        )
        branch["changes_since_summary"] = int(
            branch.get("changes_since_summary", 0)
        ) + 1

    def _merge_state(
        self,
        node: JsonObject,
        screen_id: str,
        analysis: JsonObject,
    ) -> JsonObject:

        state_name = (
            self._clean_string(analysis.get("state_name"))
            or "default"
        )
        states = node["dossier"]["states"]
        state = next(
            (
                item
                for item in states
                if item.get("name", "").casefold()
                == state_name.casefold()
            ),
            None,
        )

        if state is None:
            state = {
                "state_id": (
                    f"{node['node_id']}_state_{len(states) + 1}"
                ),
                "name": state_name,
                "description": "",
                "screen_ids": [],
                "facts": [],
                "first_seen_at": utc_now(),
                "last_seen_at": utc_now(),
            }
            states.append(state)

        state["description"] = (
            self._clean_string(analysis.get("state_description"))
            or state.get("description")
            or ""
        )
        self._append_unique(state["screen_ids"], screen_id)
        self._extend_unique(state["facts"], analysis.get("facts", []))
        state["last_seen_at"] = utc_now()

        return state

    def _merge_controls(
        self,
        node: JsonObject,
        controls: Any,
    ) -> None:

        if not isinstance(controls, list):
            return

        dossier_controls = node["dossier"]["controls"]

        for candidate in controls:

            if not isinstance(candidate, dict):
                continue

            element_id = self._clean_string(candidate.get("element_id"))
            label = self._clean_string(candidate.get("label"))
            stable_id = (
                self._clean_string(candidate.get("control_id"))
                or element_id
                or self._slug(label)
            )

            if not stable_id:
                continue

            control = next(
                (
                    item
                    for item in dossier_controls
                    if item.get("control_id") == stable_id
                    or (
                        element_id
                        and element_id in item.get("element_ids_seen", [])
                    )
                ),
                None,
            )

            if control is None:
                control = {
                    "control_id": stable_id,
                    "element_ids_seen": [],
                    "label": label,
                    "control_type": "unknown",
                    "location": "",
                    "function": "",
                    "interaction_types": [],
                    "enabled_conditions": [],
                    "observed_outcomes": [],
                    "confidence": 0.0,
                }
                dossier_controls.append(control)

            if element_id:
                self._append_unique(
                    control["element_ids_seen"],
                    element_id,
                )

            for field in (
                "label",
                "control_type",
                "location",
                "function",
            ):
                value = self._clean_string(candidate.get(field))

                if value:
                    control[field] = value

            self._extend_unique(
                control["interaction_types"],
                candidate.get(
                    "interactions",
                    candidate.get("interaction_types", []),
                ),
            )
            self._extend_unique(
                control["enabled_conditions"],
                candidate.get("enabled_conditions", []),
            )
            confidence = candidate.get("confidence")

            if isinstance(confidence, (int, float)) and not isinstance(
                confidence,
                bool,
            ):
                control["confidence"] = max(
                    control.get("confidence", 0.0),
                    max(0.0, min(1.0, float(confidence))),
                )

    def _enrich_incoming_transition(
        self,
        node_id: str,
        screen_id: str,
        understanding: Any,
    ) -> None:

        if not isinstance(understanding, dict):
            return

        edge = next(
            (
                item
                for item in reversed(self.graph["edges"])
                if item.get("to_node_id") == node_id
                and item.get("evidence", {}).get("after_screen_id")
                == screen_id
            ),
            None,
        )

        if edge is None:
            return

        for field in (
            "functional_effect",
            "workflow_role",
            "return_action",
        ):
            value = self._clean_string(understanding.get(field))

            if value:
                edge[field] = value

        for field in ("preconditions", "side_effects"):
            self._extend_unique(edge[field], understanding.get(field, []))

        reversible = understanding.get("reversible")

        if isinstance(reversible, bool):
            edge["reversible"] = reversible

    def _apply_application_update(self, update: Any) -> None:

        if not isinstance(update, dict):
            return

        summary = self._clean_string(update.get("app_summary"))

        if summary:
            self.knowledge["app_summary"] = summary

        for field in (
            "features",
            "domain_objects",
            "global_ui_patterns",
            "navigation_model",
            "failures",
            "open_questions",
            "notes",
        ):
            self._extend_unique(
                self.knowledge[field],
                update.get(field, []),
            )

        for fact in update.get("facts", []):

            if not isinstance(fact, str):
                continue

            if re.search(
                r"\b(should|likely|may|might|expected|probably|perhaps)\b",
                fact,
                re.I,
            ):
                self._extend_unique(
                    self.knowledge["hypotheses"],
                    [fact],
                )

            else:
                self._extend_unique(self.knowledge["facts"], [fact])

        self._extend_unique(
            self.knowledge["hypotheses"],
            update.get("hypotheses", []),
        )
        self._merge_workflows(
            self.knowledge["workflows"],
            update.get("workflows", []),
        )
        self.knowledge["updated_at"] = utc_now()

    def _refresh_frontier(self) -> None:

        frontier = []

        for node in self.graph["nodes"]:

            for action in node.get("actions", []):

                if action.get("status") != "unexplored":
                    continue

                frontier.append(
                    {
                        "node_id": node["node_id"],
                        "screen_name": (
                            node.get("semantic_name")
                            or node.get("summary")
                            or node["node_id"]
                        ),
                        "action_key": action.get("action_key"),
                        "label": action.get("label"),
                        "visit_count": node.get("visit_count", 0),
                    }
                )

        self.graph["frontier"] = frontier

    def _relevant_frontier(
        self,
        current_node_id: str,
        neighbor_ids: list[str],
    ) -> list[JsonObject]:

        local = [
            item
            for item in self.graph["frontier"]
            if item.get("node_id")
            in {current_node_id, *neighbor_ids}
        ]
        distant = [
            item
            for item in self.graph["frontier"]
            if item not in local
        ]

        return (local + distant)[:25]

    def _application_overview(self) -> JsonObject:

        return {
            "app_summary": self.knowledge["app_summary"],
            "features": self.knowledge["features"],
            "domain_objects": self.knowledge["domain_objects"],
            "global_ui_patterns": self.knowledge[
                "global_ui_patterns"
            ],
            "navigation_model": self.knowledge["navigation_model"],
            "confirmed_workflows": self.knowledge["workflows"],
            "failures": self.knowledge["failures"],
            "open_questions": self.knowledge["open_questions"],
        }

    def _compact_node(self, node: JsonObject) -> JsonObject:

        dossier = node.get("dossier", {})

        return {
            "node_id": node["node_id"],
            "semantic_name": node.get("semantic_name"),
            "screen_type": node.get("screen_type"),
            "summary": node.get("summary"),
            "purpose": node.get("purpose"),
            "states": [
                {
                    "state_id": state.get("state_id"),
                    "name": state.get("name"),
                    "description": state.get("description"),
                }
                for state in dossier.get("states", [])
            ],
            "facts": dossier.get("facts", [])[-10:],
            "open_questions": dossier.get("open_questions", [])[-10:],
            "actions_remaining": len(
                [
                    action
                    for action in node.get("actions", [])
                    if action.get("status") == "unexplored"
                ]
            ),
        }

    def _screen_summary(self, node: JsonObject) -> str:

        name = node.get("semantic_name") or node["node_id"]
        purpose = node["dossier"]["functional"].get("purpose") or ""
        layout = node["dossier"]["ui"].get("layout_summary") or ""

        return " — ".join(
            part
            for part in (name, purpose, layout)
            if part
        )

    def _branch_frontier(self, branch: JsonObject) -> list[JsonObject]:

        return [
            item
            for item in self.graph["frontier"]
            if item.get("node_id") in branch.get("node_ids", [])
        ]

    def _branch(self, branch_id: str) -> JsonObject | None:

        return next(
            (
                branch
                for branch in self.graph["branches"]
                if branch.get("branch_id") == branch_id
            ),
            None,
        )

    def _ancestor_branch_context(
        self,
        branch: JsonObject,
    ) -> list[JsonObject]:

        ancestors: list[JsonObject] = []
        parent_id = branch.get("parent_branch_id")
        visited: set[str] = set()

        while isinstance(parent_id, str) and parent_id not in visited:

            visited.add(parent_id)
            parent = self._branch(parent_id)

            if parent is None:
                break

            ancestors.append(
                {
                    "branch_id": parent.get("branch_id"),
                    "root_node_id": parent.get("root_node_id"),
                    "entry_action_key": parent.get("entry_action_key"),
                    "summary": parent.get("summary"),
                    "current_goal": parent.get("current_goal"),
                    "return_plan": parent.get("return_plan"),
                    "path": parent.get("path", []),
                    "unresolved_actions": len(
                        self._branch_frontier(parent)
                    ),
                }
            )
            parent_id = parent.get("parent_branch_id")

        ancestors.reverse()

        return ancestors

    def _node_by_identity(self, identity: str) -> JsonObject | None:

        return next(
            (
                node
                for node in self.graph["nodes"]
                if node.get("identity") == identity
                or identity in node.get("fingerprints", [])
            ),
            None,
        )

    def _visit_by_screen_id(self, screen_id: str) -> JsonObject | None:

        return next(
            (
                visit
                for visit in self.graph["visits"]
                if visit.get("screen_id") == screen_id
            ),
            None,
        )

    def _has_node(self, node_id: str) -> bool:

        return any(
            node.get("node_id") == node_id
            for node in self.graph["nodes"]
        )

    @staticmethod
    def _action(node: JsonObject, action_key: str) -> JsonObject | None:

        return next(
            (
                action
                for action in node.get("actions", [])
                if action.get("action_key") == action_key
            ),
            None,
        )

    def _merge_available_actions(
        self,
        node: JsonObject,
        context_text: str,
    ) -> None:

        for element_id, action_name, label in ACTION_PATTERN.findall(
            context_text
        ):
            action_key = f"{action_name}:{element_id}"

            if self._action(node, action_key) is None:
                node["actions"].append(
                    {
                        "action_key": action_key,
                        "action": action_name,
                        "element_id": element_id,
                        "label": label,
                        "status": "unexplored",
                        "attempts": 0,
                        "outcomes": [],
                        "blocked_reason": None,
                    }
                )

    def _merge_named_objects(
        self,
        target: list[Any],
        values: Any,
        *,
        keys: tuple[str, ...],
    ) -> None:

        if not isinstance(values, list):
            return

        for value in values:

            if not isinstance(value, dict):
                continue

            identity = next(
                (
                    self._clean_string(value.get(key))
                    for key in keys
                    if self._clean_string(value.get(key))
                ),
                None,
            )

            if not identity:
                self._append_unique(target, value)
                continue

            existing = next(
                (
                    item
                    for item in target
                    if isinstance(item, dict)
                    and any(
                        self._clean_string(item.get(key)).casefold()
                        == identity.casefold()
                        for key in keys
                        if self._clean_string(item.get(key))
                    )
                ),
                None,
            )

            if existing is None:
                target.append(dict(value))

            else:
                existing.update(
                    {
                        key: item
                        for key, item in value.items()
                        if item not in (None, "", [], {})
                    }
                )

    def _merge_workflows(
        self,
        target: list[Any],
        values: Any,
    ) -> None:

        self._merge_named_objects(
            target,
            values,
            keys=("name",),
        )

    @staticmethod
    def _extend_unique(target: list[Any], values: Any) -> None:

        if not isinstance(values, list):
            return

        for value in values:
            ExplorationGraphStore._append_unique(target, value)

    @staticmethod
    def _append_unique(target: list[Any], value: Any) -> None:

        if value in (None, "", [], {}):
            return

        marker = json.dumps(value, sort_keys=True, ensure_ascii=False)

        if not any(
            json.dumps(item, sort_keys=True, ensure_ascii=False) == marker
            for item in target
        ):
            target.append(value)

    @staticmethod
    def _unique_values(values: Any) -> list[Any]:

        result: list[Any] = []
        ExplorationGraphStore._extend_unique(result, values)

        return result

    @staticmethod
    def _clean_string(value: Any) -> str:

        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _slug(value: str) -> str:

        return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")

    def _touch(self) -> None:

        self.graph["updated_at"] = utc_now()
        self.knowledge["updated_at"] = utc_now()

    @staticmethod
    def _write(path: Path, value: JsonObject) -> None:

        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")

        try:
            temporary.write_text(
                json.dumps(value, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(path)

        finally:
            temporary.unlink(missing_ok=True)
