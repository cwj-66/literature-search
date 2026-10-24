import { Component, type ReactNode } from "react";

export class AppErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return <main className="app-startup">
      <p role="alert">页面暂时未能加载，请重新加载后再试。</p>
      <button type="button" onClick={() => window.location.reload()}>重新加载</button>
    </main>;
    return this.props.children;
  }
}
