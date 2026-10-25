import { lazy, Suspense } from "react";
import { Button, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { BrowserRouter, Link, Navigate, Route, Routes } from "react-router-dom";
import { AppLayout } from "./components/AppLayout";
import { antdTheme } from "./theme";

const HomePage = lazy(() => import("./pages/HomePage"));
const WorkspaceHome = lazy(() => import("./pages/workspace/HomePage"));
const SearchPage = lazy(() => import("./pages/SearchPage"));
const LibraryPage = lazy(() => import("./pages/LibraryPage"));
const PaperPage = lazy(() => import("./pages/PaperPage"));
const RecommendPage = lazy(() => import("./pages/RecommendPage"));
const KnowledgeGraphPage = lazy(() => import("./pages/KnowledgeGraphPage"));
const StudioPage = lazy(() => import("./pages/StudioPage"));
const AssistantPage = lazy(() => import("./pages/workspace/AssistantPage"));
const WorkspacePapersPage = lazy(() => import("./pages/workspace/PapersPage"));
const WorkspaceComparePage = lazy(() => import("./pages/workspace/ComparePage"));
const ContentComparePage = lazy(() => import("./pages/workspace/ContentComparePage"));
const ResearchFieldsPage = lazy(() => import("./pages/workspace/ResearchFieldsPage"));
const WorkspaceGraphPage = lazy(() => import("./pages/workspace/GraphPage"));
const PaperReadPage = lazy(() => import("./pages/workspace/PaperReadPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));

export default function App() {
  return (
    <ConfigProvider button={{ autoInsertSpace: false }} locale={zhCN} theme={antdTheme}>
      <BrowserRouter>
        <AppLayout>
          <Suspense fallback={<div className="route-loading" role="status">正在加载页面…</div>}>
            <Routes>
              <Route path="/" element={<HomePage />} />
              <Route path="/w/:id" element={<WorkspaceHome />} />
              <Route path="/w/:id/home" element={<WorkspaceHome />} />
              <Route path="/w/:id/search" element={<SearchPage />} />
              <Route path="/w/:id/studio" element={<StudioPage />} />
              <Route path="/w/:id/research" element={<AssistantPage />} />
              <Route path="/w/:id/library" element={<WorkspacePapersPage />} />
              <Route path="/w/:id/compare" element={<WorkspaceComparePage />} />
              <Route path="/w/:id/compare/table" element={<ContentComparePage />} />
              <Route path="/w/:id/fields" element={<ResearchFieldsPage />} />
              <Route path="/w/:id/graph" element={<WorkspaceGraphPage />} />
              <Route path="/w/:id/p/:paperKey" element={<PaperReadPage />} />
              <Route path="/search" element={<SearchPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/library" element={<LibraryPage />} />
              <Route path="/studio" element={<Navigate to="/" replace />} />
              <Route path="/paper" element={<PaperPage />} />
              <Route path="/recommend" element={<RecommendPage />} />
              <Route path="/graph" element={<KnowledgeGraphPage />} />
              <Route path="*" element={<main className="page page-narrow"><div className="page-missing"><div className="page-missing-inner"><p className="page-missing-code">404</p><h1>这个页面不存在</h1><p>链接可能已经失效，或者对应的研究工作区已被删除。</p><Button type="primary" size="large"><Link to="/">返回研究工作区</Link></Button></div></div></main>} />
            </Routes>
          </Suspense>
        </AppLayout>
      </BrowserRouter>
    </ConfigProvider>
  );
}
