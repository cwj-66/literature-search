import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import LibraryPage from "./pages/LibraryPage";
import PaperPage from "./pages/PaperPage";
import SearchPage from "./pages/SearchPage";

export default function App() {
  return (
    <ConfigProvider button={{ autoInsertSpace: false }} locale={zhCN} theme={{ token: { colorPrimary: "#1f4b99", borderRadius: 8 } }}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<SearchPage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/paper" element={<PaperPage />} />
          <Route path="*" element={<main className="wrap detail"><h1>页面不存在</h1><a href="/">返回检索</a></main>} />
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  );
}
