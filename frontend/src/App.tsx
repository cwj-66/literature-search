import { lazy, Suspense } from "react";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { AppLayout } from "./components/AppLayout";
import { antdTheme } from "./theme";

const SearchPage = lazy(() => import("./pages/SearchPage"));
const LibraryPage = lazy(() => import("./pages/LibraryPage"));
const PaperPage = lazy(() => import("./pages/PaperPage"));
const RecommendPage = lazy(() => import("./pages/RecommendPage"));
const KnowledgeGraphPage = lazy(() => import("./pages/KnowledgeGraphPage"));
const StudioPage = lazy(() => import("./pages/StudioPage"));

export default function App() {
  return (
    <ConfigProvider button={{ autoInsertSpace: false }} locale={zhCN} theme={antdTheme}>
      <BrowserRouter>
        <AppLayout>
          <Suspense fallback={<div className="route-loading" role="status">正在加载页面…</div>}>
            <Routes>
              <Route path="/" element={<SearchPage />} />
              <Route path="/library" element={<LibraryPage />} />
              <Route path="/studio" element={<StudioPage />} />
              <Route path="/paper" element={<PaperPage />} />
              <Route path="/recommend" element={<RecommendPage />} />
              <Route path="/graph" element={<KnowledgeGraphPage />} />
              <Route path="*" element={<main className="page page-narrow"><h1 className="page-title">页面不存在</h1><Link to="/">返回检索</Link></main>} />
            </Routes>
          </Suspense>
        </AppLayout>
      </BrowserRouter>
    </ConfigProvider>
  );
}
