"""Loop control harness: alternates the Performer and the Evolver until the task is solved."""
import json

from . import toolbox, performer_agent, evolver_agent


def _format_output(output):
    """Pretty-print dicts and lists as indented JSON on their own lines; leave scalars inline."""
    if isinstance(output, (dict, list, tuple)):
        return "\n" + json.dumps(output, indent=2, ensure_ascii=False, default=str)
    return output


def main(target_task=None):
    # Default task when none is given
    if target_task is None:
        target_task = "Compute the sum of all primes numbers less than 50."
    max_generations = 3

    toolbox.setup_environment()

    # One extra performer pass so the tool written in the last generation still gets run
    for generation in range(max_generations + 1):
        print(f"\n================= GENERATION CYCLE {generation + 1} =================")

        # Reload all tools, including any the Evolver wrote or changed last generation
        tools_inventory = toolbox.load_agent_tools()

        # Choose a tool and run it
        execution_report = performer_agent.run_performer(target_task, tools_inventory)

        if execution_report["status"] == "success":
            print(f"\n🚀 SUCCESS! The self-evolved code executed correctly.")
            print(f"Final Code Output: {_format_output(execution_report['output'])}")
            return
        if generation == max_generations:
            break
        # Calling mistakes the Performer couldn't recover from leave no tool to fix: just try again
        if execution_report.get("fault") == "caller":
            print("\n[Harness] No tool to fix after repeated calling mistakes; moving to the next generation.")
            continue
        # Tool or capability fault: have the Evolver create, fix or extend a tool
        evolver_agent.run_evolver(target_task, execution_report)

    print("\n❌ Loop halted. Max evolution generations achieved without clearing execution constraints.")
