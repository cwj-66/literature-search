import { ResearchChatSessions } from "./ResearchChatSessions";
import { MissingWorkspace, useWorkspaceProject } from "./shared";
export default function AssistantPage() {
  const { id, project } = useWorkspaceProject();
  return project ? <ResearchChatSessions key={id} id={id} name={project.name} /> : <MissingWorkspace />;
}
