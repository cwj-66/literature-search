import type { Project } from "./projects";

/** The API owns saved membership; the browser owns reading state and notes. */
export function refreshDemoProjects(cached: { projects: Project[]; currentId: string }, remote: Project[]) {
  const byId = new Map(remote.map(project => [project.id, project]));
  return { ...cached, projects: cached.projects.map(project => {
    const fresh = byId.get(project.id);
    if (!fresh) return project; // Preserve browser-only workspaces.
    const oldPapers = Object.values(project.papers);
    return { ...project, name: fresh.name, papers: Object.fromEntries(Object.entries(fresh.papers).map(([key, value]) => {
      const old = project.papers[key] ?? oldPapers.find(item => item.paper.id === value.paper.id);
      return [key, old ? { ...old, paper: value.paper, addedAt: value.addedAt } : value];
    })) };
  }) };
}
