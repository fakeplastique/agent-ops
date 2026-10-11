import { runWhileVisible } from "../motion";

// Slow folded silk: a warped-wave height field lit from a point that follows the pointer, mapped onto a 3-colour ramp.
const VS = "attribute vec2 a;void main(){gl_Position=vec4(a,0.,1.);}";
const FS = `#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 r,l,pp;uniform float t,pa;uniform vec3 c0,c1,c2;
float h(vec2 p){vec2 q=p;p+=.6*vec2(sin(p.y*1.4+t*.30),sin(p.x*1.2-t*.24));p+=.3*vec2(sin(p.y*2.9-t*.45+1.7),sin(p.x*2.6+t*.36));
float v=sin(p.x*2.1+p.y*1.3+sin(p.y*1.7+t*.22)*1.4)+.35*sin(p.x*5.2-p.y*3.1+t*.3);
if(pa>=0.){float w=length(q-pp)-pa*3.2;v+=sin(w*9.)*exp(-abs(w)*2.2)*exp(-pa*1.1)*.9;}return v;}
void main(){vec2 uv=(gl_FragCoord.xy-.5*r)/min(r.x,r.y)*4.8;float e=.012;
vec2 g=vec2(h(uv+vec2(e,0.))-h(uv-vec2(e,0.)),h(uv+vec2(0.,e))-h(uv-vec2(0.,e)))/(2.*e);
vec3 n=normalize(vec3(-g*.15,1.));float d=smoothstep(.15,.95,dot(n,normalize(vec3(l,.7))));
vec3 c=mix(c0,c1,smoothstep(0.,.5,d));c=mix(c,c2,smoothstep(.55,1.,d));
c+=(fract(sin(dot(gl_FragCoord.xy,vec2(12.9898,78.233)))*43758.5453)-.5)*.025;gl_FragColor=vec4(c,1.);}`;

export type Ramp = [number[], number[], number[]];

// Returns a stop function, or null when WebGL is unavailable (the orb then stays plain teal).
export function startSilk(cv: HTMLCanvasElement, hero: Element, o: { still: boolean; ramp: Ramp }): (() => void) | null {
  const gl = cv.getContext("webgl", { antialias: false, alpha: false, powerPreference: "low-power" });
  if (!gl) return null;
  const shader = (type: number, src: string) => {
    const s = gl.createShader(type);
    if (!s) return null;
    gl.shaderSource(s, src);
    gl.compileShader(s);
    return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
  };
  const vs = shader(gl.VERTEX_SHADER, VS);
  const fs = shader(gl.FRAGMENT_SHADER, FS);
  const pg = gl.createProgram();
  if (!vs || !fs || !pg) return null;
  gl.attachShader(pg, vs);
  gl.attachShader(pg, fs);
  gl.linkProgram(pg);
  if (!gl.getProgramParameter(pg, gl.LINK_STATUS)) return null;
  gl.useProgram(pg);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const a = gl.getAttribLocation(pg, "a");
  gl.enableVertexAttribArray(a);
  gl.vertexAttribPointer(a, 2, gl.FLOAT, false, 0, 0);
  const U = (n: string) => gl.getUniformLocation(pg, n);
  o.ramp.forEach((c, i) => gl.uniform3fv(U(`c${i}`), c));
  const [uR, uT, uL, uPP, uPA] = ["r", "t", "l", "pp", "pa"].map(U);

  const light = [-0.5, 0.6];
  const goal = [-0.5, 0.6];
  let clock = o.still ? 4 : 3;
  let press: { x: number; y: number; t: number } | null = null;

  const size = () => {
    // The silk is smooth: shade at 0.6x and let the browser scale it up.
    const d = Math.min(720, Math.round(cv.offsetWidth * 0.6)) || 1;
    cv.width = cv.height = d;
    gl.viewport(0, 0, d, d);
    gl.uniform2f(uR, d, d);
  };
  const draw = () => {
    gl.uniform1f(uT, clock);
    gl.uniform2f(uL, light[0], light[1]);
    gl.uniform2f(uPP, press?.x ?? 0, press?.y ?? 0);
    gl.uniform1f(uPA, press ? clock - press.t : -1);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };
  size();
  draw();
  const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => (size(), draw()));
  ro?.observe(cv);
  if (o.still) return () => ro?.disconnect(); // reduced motion: one frame, no loop, no pointer handling

  const onDown = (e: Event) => {
    const ev = e as PointerEvent;
    if ((ev.target as Element).closest("a,button,input,summary,.ln-frag")) return;
    const b = cv.getBoundingClientRect();
    const x = (ev.clientX - b.left) / b.width;
    const y = 1 - (ev.clientY - b.top) / b.height;
    if (Math.hypot(x - 0.5, y - 0.5) <= 0.5) press = { x: (x - 0.5) * 4.8, y: (y - 0.5) * 4.8, t: clock };
  };
  const onMove = (e: Event) => {
    const ev = e as PointerEvent;
    if (ev.pointerType !== "mouse") return;
    goal[0] = (ev.clientX / innerWidth - 0.5) * 1.8;
    goal[1] = -(ev.clientY / innerHeight - 0.5) * 1.8 + 0.2;
  };
  hero.addEventListener("pointerdown", onDown);
  hero.addEventListener("pointermove", onMove);
  const stop = runWhileVisible(
    hero,
    (_now, dt) => {
      clock += dt;
      const k = Math.min(1, dt * 3);
      light[0] += (goal[0] - light[0]) * k;
      light[1] += (goal[1] - light[1]) * k;
      if (press && clock - press.t > 4) press = null;
      draw();
    },
    32, // 30 fps is plenty for slow silk
  );
  return () => {
    stop();
    ro?.disconnect();
    hero.removeEventListener("pointerdown", onDown);
    hero.removeEventListener("pointermove", onMove);
  };
}
