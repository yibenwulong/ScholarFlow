import type { Run } from '@/types/scholarflow';

const byCreation = (left: Run, right: Run) => Date.parse(left.created_at) - Date.parse(right.created_at) || left.id.localeCompare(right.id);

export function latestResearchRun(runs: Run[]): Run | undefined {
  return runs.filter(run => run.kind === 'literature_review').sort(byCreation).at(-1);
}

export function runForProgress(runs: Run[]): Run | undefined {
  const ordered = [...runs].sort(byCreation);
  const active = ordered.find(run => run.status === 'running') || ordered.find(run => run.status === 'queued');
  if (active) return active;
  const research = latestResearchRun(ordered);
  // A later explicit recheck can need attention after completed research.
  const latestCheck = ordered.filter(run => run.kind === 'report_check').at(-1);
  const check = (!research || research.status === 'completed') && latestCheck && ['needs_review', 'failed'].includes(latestCheck.status)
    && (!research || byCreation(latestCheck, research) > 0) ? latestCheck : undefined;
  return check || research || ordered.at(-1);
}

function planId(run: Run): string | undefined {
  const plan = run.manifests.plan;
  return plan && typeof plan === 'object' && 'plan_revision_id' in plan && typeof plan.plan_revision_id === 'string'
    ? plan.plan_revision_id : run.pending_action?.plan_revision_id;
}

export function activeAnalysis(runs: Run[], projectId: string, approvedPlanId: string | undefined, selected: string[]): Run | undefined {
  if (!approvedPlanId || !selected.length) return;
  const versions = JSON.stringify([...selected].sort());
  return runForProgress(runs.filter(run => {
    if (run.project_id !== projectId || run.kind !== 'literature_review' || !['queued', 'running'].includes(run.status)
      || planId(run) !== approvedPlanId) return false;
    const selection = run.manifests.selection;
    if (!selection || typeof selection !== 'object' || !('paper_version_ids' in selection) || !Array.isArray(selection.paper_version_ids)) return false;
    return selection.paper_version_ids.every(value => typeof value === 'string')
      && JSON.stringify([...selection.paper_version_ids].sort()) === versions;
  }));
}

export function awaitingSelection(runs: Run[], projectId: string, approvedPlanId: string): Run | undefined {
  const latest = latestResearchRun(runs.filter(run => run.project_id === projectId));
  return latest?.status === 'waiting_for_user' && latest.pending_action?.type === 'confirm_selection' && planId(latest) === approvedPlanId
    ? latest : undefined;
}
