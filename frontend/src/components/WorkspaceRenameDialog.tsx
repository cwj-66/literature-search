import { prepareWorkspace } from "../api";
import { projects, type Project } from "../projects";
import { RenameDialog } from "./RenameDialog";

export function WorkspaceRenameDialog({ project, onClose }: { project: Project | null; onClose: () => void }) {
  return <RenameDialog open={!!project} title="重命名工作区" label="工作区名称" value={project?.name ?? ""} maxLength={40}
    onClose={onClose} onSave={async name => {
      if (!project) return;
      await prepareWorkspace(project.id, name, []);
      projects.rename(name, project.id);
    }} />;
}
