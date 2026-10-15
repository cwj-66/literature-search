import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import PaperPage from "./pages/PaperPage";
import SearchPage from "./pages/SearchPage";

export default function App() {
  return (
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: "#1f4b99", borderRadius: 8 } }}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<SearchPage />} />
          <Route path="/paper" element={<PaperPage />} />
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  );
}
