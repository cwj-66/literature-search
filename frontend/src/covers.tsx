import { useId, type ReactNode } from "react";
import { DEFAULT_COVER, isCoverId, type CoverId } from "./projects";

export const COVERS: { id: CoverId; label: string }[] = [
  { id: "layers", label: "蓝色层次" },
  { id: "docs", label: "紫色文档" },
  { id: "agent", label: "智能体" },
  { id: "media", label: "多模态" },
  { id: "chart", label: "数据图表" },
  { id: "vision", label: "视觉" },
  { id: "graph", label: "关系图谱" },
  { id: "notes", label: "默认" },
];

export function WorkspaceCover({ cover, image }: { cover?: CoverId; image?: string }) {
  if (image) return <img src={image} alt="" />;
  return <CoverArt id={cover && isCoverId(cover) ? cover : DEFAULT_COVER} />;
}

export function CoverArt({ id }: { id: CoverId }) {
  const uid = `c${useId().replace(/:/g, "")}`;
  return <svg viewBox="0 0 360 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false">{scene(id, uid)}</svg>;
}

export function readCoverFile(file: File) {
  return new Promise<string>((resolve, reject) => {
    const image = new Image();
    const url = URL.createObjectURL(file);
    image.onload = () => {
      const max = 960;
      const scale = Math.min(1, max / Math.max(image.width, image.height, 1));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(image.width * scale));
      canvas.height = Math.max(1, Math.round(image.height * scale));
      const ctx = canvas.getContext("2d");
      if (!ctx) { URL.revokeObjectURL(url); reject(new Error("canvas")); return; }
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL("image/jpeg", 0.82));
    };
    image.onerror = () => { URL.revokeObjectURL(url); reject(new Error("image")); };
    image.src = url;
  });
}

function Bg({ uid, from, to }: { uid: string; from: string; to: string }) {
  return <>
    <defs>
      <linearGradient id={`${uid}-bg`} x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stopColor={from} />
        <stop offset="100%" stopColor={to} />
      </linearGradient>
    </defs>
    <rect width="360" height="200" fill={`url(#${uid}-bg)`} />
  </>;
}

function scene(id: CoverId, uid: string): ReactNode {
  if (id === "layers") return <>
    <Bg uid={uid} from="#e4efff" to="#f7faff" />
    <rect x="118" y="30" width="168" height="112" rx="16" fill="#d5e4fb" />
    <rect x="96" y="44" width="168" height="112" rx="16" fill="#c0d6f8" />
    <rect x="74" y="58" width="168" height="112" rx="16" fill="#ffffff" />
    <rect x="92" y="80" width="72" height="8" rx="4" fill="#d3e2f6" />
    <rect x="92" y="98" width="118" height="6" rx="3" fill="#e6eef8" />
    <rect x="92" y="112" width="96" height="6" rx="3" fill="#e6eef8" />
    <rect x="92" y="126" width="78" height="6" rx="3" fill="#e6eef8" />
  </>;
  if (id === "docs") return <>
    <Bg uid={uid} from="#efe8ff" to="#fbf9ff" />
    <g transform="translate(198 34) rotate(8)">
      <rect width="108" height="132" rx="14" fill="#ddd2fb" />
    </g>
    <g transform="translate(62 36) rotate(-7)">
      <rect width="132" height="142" rx="14" fill="#ffffff" />
      <rect x="18" y="24" width="62" height="8" rx="4" fill="#ddd4f7" />
      <rect x="18" y="44" width="96" height="6" rx="3" fill="#eee8fb" />
      <rect x="18" y="58" width="84" height="6" rx="3" fill="#eee8fb" />
      <rect x="18" y="72" width="72" height="6" rx="3" fill="#eee8fb" />
    </g>
    <rect x="214" y="122" width="96" height="42" rx="14" fill="#ffffff" />
    <rect x="230" y="139" width="48" height="6" rx="3" fill="#e3daf8" />
  </>;
  if (id === "agent") return <>
    <Bg uid={uid} from="#fff0e4" to="#fff9f5" />
    <rect x="42" y="64" width="92" height="58" rx="16" fill="#ffffff" />
    <rect x="56" y="80" width="40" height="6" rx="3" fill="#ffd8bf" />
    <rect x="56" y="94" width="58" height="6" rx="3" fill="#ffe7d8" />
    <rect x="248" y="52" width="74" height="40" rx="14" fill="#ffffff" />
    <rect x="262" y="68" width="40" height="6" rx="3" fill="#ffdcc6" />
    <g transform="translate(156 42)">
      <rect x="28" y="0" width="6" height="14" rx="3" fill="#f0b48f" />
      <circle cx="31" cy="2" r="5" fill="#f0b48f" />
      <rect y="16" width="78" height="64" rx="20" fill="#ffc7a4" />
      <circle cx="26" cy="44" r="5" fill="#fff" />
      <circle cx="52" cy="44" r="5" fill="#fff" />
      <rect x="16" y="86" width="46" height="26" rx="12" fill="#ffb88a" />
    </g>
  </>;
  if (id === "media") return <>
    <Bg uid={uid} from="#ffe6ee" to="#fff8f8" />
    <rect x="40" y="36" width="176" height="128" rx="16" fill="#ffffff" />
    <g clipPath={`url(#${uid}-photo)`}>
      <rect x="54" y="50" width="148" height="100" rx="12" fill="#ffd0dc" />
      <circle cx="164" cy="74" r="10" fill="#fff7f8" />
      <path d="M54 132l42-34 34 26 28-20 52 36v10H54z" fill="#f3a8bb" />
    </g>
    <defs><clipPath id={`${uid}-photo`}><rect x="54" y="50" width="148" height="100" rx="12" /></clipPath></defs>
    <rect x="198" y="78" width="118" height="74" rx="16" fill="#ffffff" />
    <circle cx="230" cy="115" r="16" fill="#ffe3eb" />
    <path d="M226 108l12 7-12 7z" fill="#e9899f" />
    <rect x="256" y="104" width="44" height="6" rx="3" fill="#ffd4e0" />
    <rect x="256" y="118" width="32" height="6" rx="3" fill="#ffe6ed" />
  </>;
  if (id === "chart") return <>
    <Bg uid={uid} from="#e6f7ee" to="#f7fcf8" />
    <rect x="54" y="32" width="252" height="136" rx="18" fill="#ffffff" />
    <rect x="86" y="112" width="26" height="32" rx="6" fill="#c6ead4" />
    <rect x="124" y="88" width="26" height="56" rx="6" fill="#8ed4ad" />
    <rect x="162" y="64" width="26" height="80" rx="6" fill="#52b788" />
    <rect x="200" y="96" width="26" height="48" rx="6" fill="#95d5b2" />
    <path d="M99 100c28-28 52-16 78-36 20-14 36-18 62-8" fill="none" stroke="#2d6a4f" strokeWidth="3" strokeLinecap="round" />
    <circle cx="239" cy="56" r="4.5" fill="#2d6a4f" />
  </>;
  if (id === "vision") return <>
    <Bg uid={uid} from="#e3f2ff" to="#f7fbff" />
    <rect x="64" y="28" width="232" height="146" rx="18" fill="#ffffff" />
    <g clipPath={`url(#${uid}-frame)`}>
      <rect x="80" y="44" width="200" height="114" fill="#d7ecff" />
      <circle cx="230" cy="74" r="14" fill="#ffffff" />
      <path d="M80 150l58-48 40 30 36-24 66 48v8H80z" fill="#8ec5f0" />
      <path d="M80 158l78-36 48 22 74-28v14H80z" fill="#5aa6e0" />
    </g>
    <defs><clipPath id={`${uid}-frame`}><rect x="80" y="44" width="200" height="114" rx="12" /></clipPath></defs>
  </>;
  if (id === "graph") return <>
    <Bg uid={uid} from="#eee9ff" to="#faf8ff" />
    <path d="M108 118l62-52 78 18-18 56-72 12z" fill="none" stroke="#d5c9f6" strokeWidth="2.4" />
    <path d="M170 66l-8 86M250 86l52 28" fill="none" stroke="#d5c9f6" strokeWidth="2.4" />
    <circle cx="108" cy="118" r="16" fill="#ffffff" />
    <circle cx="170" cy="66" r="18" fill="#cfc0f6" />
    <circle cx="248" cy="84" r="14" fill="#ffffff" />
    <circle cx="230" cy="140" r="16" fill="#ffffff" />
    <circle cx="162" cy="152" r="13" fill="#ffffff" />
    <circle cx="302" cy="114" r="11" fill="#e4dcfc" />
  </>;
  return <>
    <Bg uid={uid} from="#e7edf5" to="#f8f9fb" />
    <circle cx="262" cy="62" r="42" fill="#e1e7f0" />
    <rect x="58" y="46" width="176" height="116" rx="16" fill="#ffffff" />
    <rect x="78" y="68" width="78" height="8" rx="4" fill="#d5dde8" />
    <rect x="78" y="88" width="124" height="6" rx="3" fill="#e6ebf2" />
    <rect x="78" y="104" width="108" height="6" rx="3" fill="#e6ebf2" />
    <rect x="78" y="120" width="72" height="6" rx="3" fill="#e6ebf2" />
    <rect x="214" y="108" width="92" height="52" rx="14" fill="#ffffff" />
  </>;
}
