import { Suspense } from 'react';
import ProjectWorkspace from '@/components/scholarflow/ProjectWorkspace';
export default function ProjectPage({ params }: { params: { projectId: string } }) {
  return <Suspense fallback={<div className="sf-container">正在载入研究项目…</div>}><ProjectWorkspace projectId={params.projectId} /></Suspense>;
}
