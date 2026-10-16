import { lazy, Suspense } from "react";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppLayout } from "./components/AppLayout";

const SearchPage = lazy(() => import("./pages/SearchPage"));
const LibraryPage = lazy(() => import("./pages/LibraryPage"));
const PaperPage = lazy(() => import("./pages/PaperPage"));
const RecommendPage = lazy(() => import("./pages/RecommendPage"));
const KnowledgeGraphPage = lazy(() => import("./pages/KnowledgeGraphPage"));

export default function App() {
  return (
    <ConfigProvider button={{ autoInsertSpace: false }} locale={zhCN} theme={{ token: { colorPrimary: "#315bc0", borderRadius: 9, fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif', controlHeight: 38 } }}>
      <BrowserRouter>
        <AppLayout>
        <Suspense fallback={<div className="route-loading" role="status">正在加载页面…</div>}>
        <Routes>
          <Route path="/" element={<SearchPage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/paper" element={<PaperPage />} />
          <Route path="/recommend" element={<RecommendPage />} />
          <Route path="/graph" element={<KnowledgeGraphPage />} />
          <Route path="*" element={<main className="wrap detail"><h1>页面不存在</h1><a href="/">返回检索</a></main>} />
        </Routes>
        </Suspense>
        </AppLayout>
      </BrowserRouter>
    </ConfigProvider>
  );
}
