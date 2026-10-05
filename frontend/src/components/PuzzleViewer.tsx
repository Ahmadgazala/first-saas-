"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import type { ModelPiece, PuzzleModelData } from "@/lib/types";

/**
 * Renders the generated structure from model.json: every piece is the exact exported outline
 * (with its slots) extruded by the real material thickness and placed with the piece's 3D
 * basis. Supports assembled / exploded views, piece selection and connected-piece highlighting.
 */
interface Props {
  model: PuzzleModelData;
  className?: string;
  selectedId?: string | null;
  onSelect?: (id: string | null) => void;
}

const ROLE_COLOR: Record<string, number> = {
  spine: 0xc99a62,
  profile: 0xd9b27e,
  detail: 0xe3c191,
  cross: 0xc08a54,
};
/** Fossil kits are shown in white material, like a white laser-cut skeleton kit. */
const KIT_WHITE = 0xf2f0ea;
const KIT_EDGE = 0x8a857c;
const SELECTED = new THREE.Color(0xea4f1d);
const CONNECTED = new THREE.Color(0x2563eb);
const DIMMED = new THREE.Color(0xd9d4cc);

function hasWebGL(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

/** Deterministic slight tone variation so neighbouring sheets read as separate pieces. */
function woodTone(piece: ModelPiece, index: number): THREE.Color {
  const jitter = (((index * 2654435761) >>> 0) % 1000) / 1000 - 0.5;
  if (piece.style === "kit") return new THREE.Color(KIT_WHITE).offsetHSL(0, 0, jitter * 0.03);
  const base = new THREE.Color(ROLE_COLOR[piece.role] ?? ROLE_COLOR.cross);
  return base.offsetHSL(jitter * 0.012, jitter * 0.05, jitter * 0.05);
}

interface MeshEntry {
  piece: ModelPiece;
  mesh: THREE.Mesh;
  material: THREE.MeshStandardMaterial;
  tone: THREE.Color;
  base: THREE.Vector3;
  offset: THREE.Vector3;
  centre: THREE.Vector3; // local-space centre for labels
}

export function PuzzleViewer({ model, className = "", selectedId = null, onSelect }: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const labelsRef = useRef<HTMLDivElement>(null);
  const resetRef = useRef<() => void>(() => {});
  const explodeRef = useRef<(f: number) => void>(() => {});
  const highlightRef = useRef<(id: string | null) => void>(() => {});
  const onSelectRef = useRef(onSelect);
  const [explode, setExplode] = useState(0);
  const [hovered, setHovered] = useState<string | null>(null);
  const [supported] = useState(hasWebGL); // client-only component (next/dynamic, ssr: false)
  const explodeTarget = useRef(0);
  const explodeValue = useRef(0);

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    const mount = mountRef.current;
    const labelLayer = labelsRef.current;
    if (!mount || !labelLayer || !supported) return;
    const width = mount.clientWidth || 600;
    const height = mount.clientHeight || 420;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, height);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.add(new THREE.HemisphereLight(0xfffaf2, 0x7a6a56, 1.35));
    const sun = new THREE.DirectionalLight(0xffffff, 1.9);
    sun.castShadow = true;
    scene.add(sun);
    scene.add(sun.target);
    const fill = new THREE.DirectionalLight(0xfff3e0, 0.45);
    fill.position.set(-1.5, 0.6, -1);
    scene.add(fill);

    const t = model.thickness_mm;
    const group = new THREE.Group();
    const entries: MeshEntry[] = [];
    model.pieces.forEach((piece, index) => {
      const shape = new THREE.Shape(piece.outer.map(([x, y]) => new THREE.Vector2(x, y)));
      shape.holes = piece.holes.map((h) => new THREE.Path(h.map(([x, y]) => new THREE.Vector2(x, y))));
      const geom = new THREE.ExtrudeGeometry(shape, { depth: t, bevelEnabled: false, curveSegments: 1 });
      geom.translate(0, 0, -t / 2);
      geom.computeBoundingBox();
      const tone = woodTone(piece, index);
      const material = new THREE.MeshStandardMaterial({ color: tone.clone(), roughness: 0.82, metalness: 0, side: THREE.DoubleSide });
      const mesh = new THREE.Mesh(geom, material);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      const { origin, u, v, n } = piece.transform;
      const basis = new THREE.Matrix4().makeBasis(
        new THREE.Vector3(...(u as [number, number, number])),
        new THREE.Vector3(...(v as [number, number, number])),
        new THREE.Vector3(...(n as [number, number, number])),
      );
      basis.setPosition(new THREE.Vector3(...(origin as [number, number, number])));
      mesh.applyMatrix4(basis);
      mesh.userData.id = piece.id;
      mesh.add(new THREE.LineSegments(
        new THREE.EdgesGeometry(geom, 30),
        new THREE.LineBasicMaterial({ color: piece.style === "kit" ? KIT_EDGE : 0x3b2a17, transparent: true, opacity: 0.5 }),
      ));
      group.add(mesh);
      entries.push({
        piece,
        mesh,
        material,
        tone,
        base: mesh.position.clone(),
        offset: new THREE.Vector3(...(piece.explode_offset as [number, number, number])),
        centre: geom.boundingBox!.getCenter(new THREE.Vector3()),
      });
    });
    scene.add(group);

    const box = new THREE.Box3().setFromObject(group);
    const center = box.getCenter(new THREE.Vector3());
    const radius = box.getBoundingSphere(new THREE.Sphere()).radius || 100;
    const floorY = box.min.y - 0.3;
    const grid = new THREE.GridHelper(radius * 4, 20, 0xd6d0c4, 0xe9e4da);
    grid.position.y = floorY - 0.2;
    scene.add(grid);
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(radius * 6, radius * 6), new THREE.ShadowMaterial({ opacity: 0.18 }));
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = floorY;
    floor.receiveShadow = true;
    scene.add(floor);
    sun.position.copy(center).add(new THREE.Vector3(radius * 0.8, radius * 2.2, radius * 1.2));
    sun.target.position.copy(center);
    const sc = sun.shadow.camera as THREE.OrthographicCamera;
    sc.left = sc.bottom = -radius * 1.8;
    sc.right = sc.top = radius * 1.8;
    sc.near = radius * 0.1;
    sc.far = radius * 6;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.bias = -0.0005;

    const camera = new THREE.PerspectiveCamera(35, width / height, radius / 50, radius * 40);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.minDistance = radius * 0.5;
    controls.maxDistance = radius * 8;

    // HTML labels for piece IDs (shown when exploded or for the selection).
    labelLayer.replaceChildren();
    const labels = new Map<string, HTMLDivElement>();
    for (const e of entries) {
      const el = document.createElement("div");
      el.textContent = e.piece.id;
      el.className =
        "pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 whitespace-nowrap rounded-md bg-ink/85 px-1.5 py-0.5 text-[10px] font-semibold text-white opacity-0 transition-opacity";
      labelLayer.appendChild(el);
      labels.set(e.piece.id, el);
    }

    let selected: string | null = null;
    const applyHighlight = (id: string | null) => {
      selected = id;
      const connected = new Set(id ? entries.find((e) => e.piece.id === id)?.piece.connected_to ?? [] : []);
      for (const e of entries) {
        const m = e.material;
        if (!id) {
          m.color.copy(e.tone);
          m.emissive.setHex(0x000000);
        } else if (e.piece.id === id) {
          m.color.copy(e.tone).lerp(SELECTED, 0.55);
          m.emissive.copy(SELECTED).multiplyScalar(0.25);
        } else if (connected.has(e.piece.id)) {
          m.color.copy(e.tone).lerp(CONNECTED, 0.35);
          m.emissive.copy(CONNECTED).multiplyScalar(0.08);
        } else {
          m.color.copy(e.tone).lerp(DIMMED, 0.65);
          m.emissive.setHex(0x000000);
        }
      }
    };
    highlightRef.current = applyHighlight;

    // Bounds of the fully exploded layout, so the camera can frame either state.
    const exploded = new THREE.Box3();
    for (const e of entries) {
      const b = new THREE.Box3().setFromObject(e.mesh);
      exploded.union(b.translate(e.offset));
    }
    const explodedCenter = exploded.getCenter(new THREE.Vector3());
    const explodedRadius = exploded.getBoundingSphere(new THREE.Sphere()).radius || radius;
    const frameView = (c: THREE.Vector3, r: number) => {
      const dist = (r / Math.sin(THREE.MathUtils.degToRad(35 / 2))) * 1.05;
      const dir = camera.position.clone().sub(controls.target).normalize();
      if (!Number.isFinite(dir.x) || dir.lengthSq() < 0.5) dir.set(0.65, 0.42, 1).normalize();
      controls.target.copy(c);
      camera.position.copy(c).addScaledVector(dir, dist);
      controls.update();
    };
    const reset = () => {
      const dir = new THREE.Vector3(0.65, 0.42, 1).normalize();
      controls.target.copy(center);
      camera.position.copy(center).addScaledVector(dir, (radius / Math.sin(THREE.MathUtils.degToRad(35 / 2))) * 1.05);
      controls.update();
    };
    reset();
    resetRef.current = reset;
    explodeRef.current = (f: number) => {
      const wasExploded = explodeTarget.current >= 0.999;
      explodeTarget.current = f;
      if (f >= 0.999 && !wasExploded) frameView(explodedCenter, explodedRadius);
      else if (f <= 0.001 && wasExploded) frameView(center, radius);
    };

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const pick = (e: PointerEvent): string | null => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(entries.map((x) => x.mesh), false)[0]?.object;
      return hit ? (hit.userData.id as string) : null;
    };
    let lastHover: string | null = null;
    const onMove = (e: PointerEvent) => {
      const id = pick(e);
      if (id !== lastHover) {
        lastHover = id;
        setHovered(id);
        renderer.domElement.style.cursor = id ? "pointer" : "grab";
      }
    };
    let down: { x: number; y: number } | null = null;
    const onDown = (e: PointerEvent) => {
      down = { x: e.clientX, y: e.clientY };
    };
    const onUp = (e: PointerEvent) => {
      if (!down) return;
      const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
      down = null;
      if (moved > 5) return; // it was an orbit drag, not a click
      const id = pick(e);
      onSelectRef.current?.(id === selected ? null : id);
    };
    renderer.domElement.addEventListener("pointermove", onMove);
    renderer.domElement.addEventListener("pointerdown", onDown);
    renderer.domElement.addEventListener("pointerup", onUp);

    const tmp = new THREE.Vector3();
    let frame = 0;
    const loop = () => {
      frame = requestAnimationFrame(loop);
      // Smoothly animate between assembled and exploded.
      const cur = explodeValue.current;
      const next = cur + (explodeTarget.current - cur) * 0.15;
      explodeValue.current = Math.abs(next - explodeTarget.current) < 1e-3 ? explodeTarget.current : next;
      for (const e of entries) e.mesh.position.copy(e.base).addScaledVector(e.offset, explodeValue.current);
      controls.update();
      renderer.render(scene, camera);
      const w = renderer.domElement.clientWidth;
      const h = renderer.domElement.clientHeight;
      const showAll = explodeValue.current > 0.35;
      for (const e of entries) {
        const el = labels.get(e.piece.id)!;
        const visible = showAll || e.piece.id === selected;
        if (!visible) {
          el.style.opacity = "0";
          continue;
        }
        tmp.copy(e.centre).applyMatrix4(e.mesh.matrixWorld).project(camera);
        el.style.left = `${((tmp.x + 1) / 2) * w}px`;
        el.style.top = `${((1 - tmp.y) / 2) * h}px`;
        el.style.opacity = tmp.z < 1 ? "1" : "0";
        el.style.background = e.piece.id === selected ? "#ea4f1d" : "";
      }
    };
    loop();

    const observer = new ResizeObserver(() => {
      const w = mount.clientWidth;
      const h = mount.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    });
    observer.observe(mount);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      renderer.domElement.removeEventListener("pointermove", onMove);
      renderer.domElement.removeEventListener("pointerdown", onDown);
      renderer.domElement.removeEventListener("pointerup", onUp);
      controls.dispose();
      scene.traverse((o) => {
        const mesh = o as THREE.Mesh;
        mesh.geometry?.dispose?.();
        const mat = mesh.material as THREE.Material | THREE.Material[] | undefined;
        if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
        else mat?.dispose?.();
      });
      renderer.dispose();
      labelLayer.replaceChildren();
      mount.removeChild(renderer.domElement);
    };
  }, [model, supported]);

  useEffect(() => explodeRef.current(explode), [explode]);
  useEffect(() => highlightRef.current(selectedId), [selectedId, model]);

  if (!supported) {
    return (
      <div className={`flex items-center justify-center rounded-2xl border border-line bg-card p-6 text-center text-sm text-muted ${className}`}>
        3D preview needs WebGL, which is not available in this browser. The rendered preview image and all files are still
        available.
      </div>
    );
  }

  const mode = explode === 0 ? "assembled" : explode === 1 ? "exploded" : "custom";
  const modeButton = (active: boolean) =>
    `rounded-lg px-2.5 py-1 text-xs font-semibold transition ${active ? "bg-ink text-white" : "text-ink-soft hover:bg-black/5"}`;
  return (
    <div className={`relative overflow-hidden rounded-2xl border border-line bg-gradient-to-b from-white to-wood-soft ${className}`}>
      <div ref={mountRef} className="h-full w-full" data-testid="puzzle-viewer" />
      <div ref={labelsRef} className="pointer-events-none absolute inset-0 overflow-hidden" />
      <div className="pointer-events-none absolute left-3 top-3 rounded-lg bg-white/85 px-2.5 py-1 text-xs font-medium text-ink-soft shadow-sm">
        {hovered ? `${hovered} · click to inspect` : `${model.pieces.length} pieces · drag to orbit · scroll to zoom · click a piece`}
      </div>
      <div className="absolute inset-x-3 bottom-3 flex flex-wrap items-center gap-2 rounded-xl bg-white/90 px-2 py-1.5 shadow-sm">
        <div role="group" aria-label="View mode" className="flex gap-1">
          <button type="button" className={modeButton(mode === "assembled")} onClick={() => setExplode(0)}>
            Assembled
          </button>
          <button type="button" className={modeButton(mode === "exploded")} onClick={() => setExplode(1)}>
            Exploded
          </button>
        </div>
        <input
          aria-label="Explode amount"
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={explode}
          onChange={(e) => setExplode(Number(e.target.value))}
          className="min-w-24 flex-1 accent-laser"
        />
        <button
          type="button"
          onClick={() => {
            setExplode(0);
            onSelectRef.current?.(null);
            resetRef.current();
          }}
          className="rounded-lg border border-line px-2.5 py-1 text-xs font-medium hover:bg-black/5"
        >
          Reset
        </button>
      </div>
    </div>
  );
}
