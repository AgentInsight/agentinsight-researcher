// components/auth/glow-background.tsx
"use client";

import { useEffect, useRef } from "react";

/**
 * 光晕背景组件 (复制自 traceability-platform)
 * - 8 个静态背景光晕 (随机位置/颜色/大小, sin/cos 漂移动画)
 * - 3 个鼠标跟踪光晕 (蓝/绿/紫, 不同滞后速度)
 * - 纯 CSS + requestAnimationFrame 实现, 零依赖
 *
 * 背景色保持 frontend 现有 (--bg-page), 光晕浮在其上
 */
interface StaticOrb {
  id: number;
  style: React.CSSProperties;
  speed: number;
  range: number;
  phaseX: number;
  phaseY: number;
}

const ORB_COLORS: [number, number, number][] = [
  [0, 82, 217],   // 蓝
  [0, 168, 112],  // 绿
  [124, 58, 237], // 紫
  [0, 180, 200],  // 青
  [200, 60, 130], // 粉
  [50, 120, 220], // 浅蓝
  [180, 80, 200], // 紫粉
];

function generateStaticOrbs(count: number): StaticOrb[] {
  const orbs: StaticOrb[] = [];
  for (let i = 0; i < count; i++) {
    const size = Math.random() * 500 + 150;
    const [r, g, b] = ORB_COLORS[i % ORB_COLORS.length];
    const gradient = `radial-gradient(circle, rgba(${r},${g},${b},0.5) 0%, rgba(${r},${g},${b},0.3) 15%, rgba(${r},${g},${b},0.12) 40%, rgba(${r},${g},${b},0.03) 65%, transparent 100%)`;
    orbs.push({
      id: i,
      style: {
        width: `${size}px`,
        height: `${size}px`,
        top: `${Math.random() * 100}%`,
        left: `${Math.random() * 100}%`,
        background: gradient,
        opacity: (Math.random() * 0.15 + 0.1).toFixed(2),
      },
      speed: Math.random() * 0.8 + 0.15,
      range: Math.random() * 30 + 10,
      phaseX: Math.random() * Math.PI * 2,
      phaseY: Math.random() * Math.PI * 2,
    });
  }
  return orbs;
}

export function GlowBackground() {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const orb1Ref = useRef<HTMLDivElement>(null);
  const orb2Ref = useRef<HTMLDivElement>(null);
  const orb3Ref = useRef<HTMLDivElement>(null);
  const staticOrbsRef = useRef<StaticOrb[]>([]);
  const staticRafRef = useRef(0);
  const rafRef = useRef(0);
  const targetRef = useRef({ x: 0, y: 0 });

  useEffect(() => {
    // 生成静态光晕
    staticOrbsRef.current = generateStaticOrbs(8);

    // 鼠标跟踪光晕配置
    const randomBetween = (min: number, max: number) => Math.random() * (max - min) + min;
    const randomSign = () => (Math.random() > 0.5 ? 1 : -1);

    const orbConfigs = [
      { el: orb1Ref, offsetX: randomBetween(15, 30) * randomSign(), offsetY: randomBetween(15, 30) * randomSign(), speed: randomBetween(0.08, 0.2) },
      { el: orb2Ref, offsetX: randomBetween(50, 80) * randomSign(), offsetY: randomBetween(50, 80) * randomSign(), speed: randomBetween(0.12, 0.28) },
      { el: orb3Ref, offsetX: randomBetween(100, 150) * randomSign(), offsetY: randomBetween(100, 150) * randomSign(), speed: randomBetween(0.18, 0.35) },
    ];

    orbConfigs.forEach((cfg) => {
      if (cfg.el.current) {
        cfg.el.current.style.transition = `transform ${cfg.speed.toFixed(2)}s ease-out`;
      }
    });

    const onMouseMove = (e: MouseEvent) => {
      targetRef.current = { x: e.clientX, y: e.clientY };
    };

    const updateOrbs = () => {
      orbConfigs.forEach((cfg) => {
        if (!cfg.el.current) return;
        const x = targetRef.current.x + cfg.offsetX;
        const y = targetRef.current.y + cfg.offsetY;
        cfg.el.current.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
      });
      rafRef.current = requestAnimationFrame(updateOrbs);
    };

    // P0-11: 一次性缓存 .static-orb DOM 引用, 避免每帧 querySelectorAll
    // 注意: 必须在 useEffect 内执行 (此时 DOM 已挂载), 不能在 render 阶段查询
    // 用稀疏数组保留索引对齐 (即便某些元素缺失, cfg[i] 仍对应 el[i])
    const orbNodeList = wrapperRef.current?.querySelectorAll<HTMLElement>(".static-orb");
    const orbElements: (HTMLElement | undefined)[] = staticOrbsRef.current.map(
      (_, i) => orbNodeList?.[i],
    );

    const animateStaticOrbs = () => {
      const now = performance.now() * 0.001;
      staticOrbsRef.current.forEach((cfg, i) => {
        const el = orbElements[i];
        if (!el) return;
        const dx = Math.sin(now * cfg.speed + cfg.phaseX) * cfg.range;
        const dy = Math.cos(now * cfg.speed * 0.7 + cfg.phaseY) * cfg.range * 0.7;
        el.style.transform = `translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px)`;
      });
      staticRafRef.current = requestAnimationFrame(animateStaticOrbs);
    };

    window.addEventListener("mousemove", onMouseMove, { passive: true });
    rafRef.current = requestAnimationFrame(updateOrbs);
    staticRafRef.current = requestAnimationFrame(animateStaticOrbs);

    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      cancelAnimationFrame(rafRef.current);
      cancelAnimationFrame(staticRafRef.current);
    };
  }, []);

  return (
    <div ref={wrapperRef} className="absolute inset-0 overflow-hidden pointer-events-none">
      {/* 静态背景光晕 */}
      <div className="absolute inset-0 z-0 overflow-hidden">
        {staticOrbsRef.current.map((orb) => (
          <div
            key={orb.id}
            className="static-orb absolute rounded-full"
            style={{
              ...orb.style,
              filter: "blur(50px)",
              willChange: "transform",
            }}
          />
        ))}
      </div>

      {/* 鼠标跟踪光晕 */}
      <div className="absolute inset-0 z-1 overflow-hidden">
        <div
          ref={orb1Ref}
          className="glow-orb absolute rounded-full"
          style={{
            width: "700px",
            height: "700px",
            background: "radial-gradient(circle, rgba(0,82,217,0.6) 0%, rgba(0,82,217,0.35) 15%, rgba(0,82,217,0.1) 40%, rgba(0,82,217,0.02) 65%, transparent 100%)",
            filter: "blur(80px)",
            opacity: 0.4,
            willChange: "transform",
            top: 0,
            left: 0,
          }}
        />
        <div
          ref={orb2Ref}
          className="glow-orb absolute rounded-full"
          style={{
            width: "350px",
            height: "350px",
            background: "radial-gradient(circle, rgba(0,168,112,0.5) 0%, rgba(0,168,112,0.3) 15%, rgba(0,168,112,0.1) 40%, rgba(0,168,112,0.02) 65%, transparent 100%)",
            filter: "blur(80px)",
            opacity: 0.4,
            willChange: "transform",
            top: 0,
            left: 0,
          }}
        />
        <div
          ref={orb3Ref}
          className="glow-orb absolute rounded-full"
          style={{
            width: "300px",
            height: "300px",
            background: "radial-gradient(circle, rgba(124,58,237,0.5) 0%, rgba(124,58,237,0.3) 15%, rgba(124,58,237,0.1) 40%, rgba(124,58,237,0.02) 65%, transparent 100%)",
            filter: "blur(80px)",
            opacity: 0.4,
            willChange: "transform",
            top: 0,
            left: 0,
          }}
        />
      </div>
    </div>
  );
}
