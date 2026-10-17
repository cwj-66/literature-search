import { Link, useParams } from "react-router-dom";
import { useProjects } from "../../projects";

export function useWorkspaceProject() {
  const { id = "" } = useParams();
  const { projects } = useProjects();
  return { id, project: projects.find(item => item.id === id) };
}

export function MissingWorkspace() {
  return <main className="page page-medium"><h1 className="page-title">找不到这个工作区</h1><Link to="/">返回工作区列表</Link></main>;
}
