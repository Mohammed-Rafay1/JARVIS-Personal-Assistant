// The orb: an energy core that changes shape with what Jarvis is doing.
//   idle      - a calm sphere, slowly breathing
//   listening - spikes rise from the surface with your voice (mic spectrum)
//   thinking  - it turns faceted and twists, the shell spins fast
//   speaking  - waves ripple across it with Jarvis's voice
// Around it: a wireframe shell, two orbit rings, a particle belt and a bright core. Colours are tone-mapped,
// so they stay vivid and saturated instead of clipping to flat white.
// API (renderer.js): setPalette(a, b, swirl, dim), setMode(state), setLevel(rms, bands), pulse(), doShake(),
// fps, level, bands.
import * as THREE from './node_modules/three/build/three.module.js';

const NOISE = /* glsl */`
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy; i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx; vec4 j=p-49.0*floor(p*ns.z*ns.z);
  vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_); vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy;
  vec4 h=1.0-abs(x)-abs(y); vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}`;

const VERT = /* glsl */`
uniform float uTime; uniform float uLevel; uniform float uSwirl; uniform float uBands[32];
uniform float uSpike; uniform float uRipple; uniform float uTwist;
varying vec3 vNormal; varying vec3 vView; varying vec3 vPosV; varying float vDisp; varying float vN;
${NOISE}
void main(){
  vec3 p = normalize(position);
  // thinking: the sphere twists about its axis, back and forth
  float tw = uTwist * sin(uTime * 1.6) * 1.1 * p.y;
  float c = cos(tw), s = sin(tw);
  p = vec3(c * p.x - s * p.z, p.y, s * p.x + c * p.z);
  float ang = atan(p.y, p.x) / 6.28318 + 0.5;
  float band = uBands[int(clamp(floor(ang * 32.0), 0.0, 31.0))];
  float t = uTime * (0.35 + uSwirl * 0.9);
  float n = snoise(p * 1.2 + vec3(t, t * 0.7, -t * 0.5));
  float n2 = snoise(p * 2.6 - vec3(t * 1.3));
  float amp = 0.035 + uLevel * 0.11;
  float d = n * amp + n2 * amp * 0.25;
  // listening: sharp spikes that rise with your voice
  float sp = pow(max(snoise(p * 4.2 + vec3(0.0, t * 1.8, t)), 0.0), 2.6);
  d += uSpike * sp * (0.06 + uLevel * 0.6 + band * 0.35);
  // speaking: rings of waves rolling over the surface
  float lat = acos(clamp(p.z, -1.0, 1.0));
  d += uRipple * sin(lat * 11.0 - uTime * 8.5) * (0.012 + uLevel * 0.085);
  d += band * uLevel * 0.06;
  vDisp = d; vN = n;
  vec4 mv = modelViewMatrix * vec4(p * (1.0 + d), 1.0);
  vNormal = normalize(normalMatrix * p);
  vPosV = mv.xyz;
  vView = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}`;

const FRAG = /* glsl */`
uniform vec3 uColorA; uniform vec3 uColorB; uniform float uLevel; uniform float uTime; uniform float uDim;
uniform float uFacet;
varying vec3 vNormal; varying vec3 vView; varying vec3 vPosV; varying float vDisp; varying float vN;
void main(){
  vec3 smoothN = normalize(vNormal);
  vec3 flatN = normalize(cross(dFdx(vPosV), dFdy(vPosV)));   // thinking: crystal facets
  vec3 nrm = normalize(mix(smoothN, flatN, uFacet));
  float fres = pow(1.0 - abs(dot(nrm, vView)), 2.1);
  float mixer = clamp(0.5 + vDisp * 3.5 + 0.25 * sin(uTime * 0.8 + nrm.y * 3.0), 0.0, 1.0);
  vec3 col = mix(uColorA, uColorB, mixer) * 0.85;
  // energy contour lines flowing over the surface
  float f = fract(vN * 4.0 + uTime * 0.22);
  float line = 1.0 - smoothstep(0.0, 0.035 + uLevel * 0.02, abs(f - 0.5));
  col += mix(uColorB, vec3(1.0), 0.25) * line * (0.30 + uLevel * 0.9);
  // facet edges light up while thinking
  float facetLight = pow(max(dot(nrm, normalize(vec3(0.4, 0.7, 1.0))), 0.0), 8.0) * uFacet;
  col += uColorB * facetLight * 1.4;
  col += fres * mix(uColorB, vec3(1.0), 0.40) * (1.25 + uLevel * 0.7);
  col *= 0.9 + uLevel * 0.55;
  col = 1.0 - exp(-col * 1.7);   // tone map: bright and saturated, never a flat white blob
  float alpha = (0.62 + fres * 0.38) * uDim;
  gl_FragColor = vec4(col, alpha);
}`;

const GLOW_VERT = /* glsl */`varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`;
const HALO_FRAG = /* glsl */`
uniform vec3 uColor; uniform float uLevel; uniform float uDim; varying vec2 vUv;
void main(){
  float r = length(vUv - 0.5) * 2.0;
  float g = smoothstep(1.0, 0.2, r) * (0.26 + uLevel * 0.55);
  gl_FragColor = vec4(uColor * g, g * uDim);
}`;
const CORE_FRAG = /* glsl */`
uniform vec3 uColor; uniform float uLevel; uniform float uDim; varying vec2 vUv;
void main(){
  float r = length(vUv - 0.5) * 2.0;
  float g = pow(max(1.0 - r, 0.0), 3.0) * (0.55 + uLevel * 1.1);
  vec3 c = mix(uColor, vec3(1.0), smoothstep(0.55, 1.0, 1.0 - r));
  gl_FragColor = vec4(c * g, g * uDim);
}`;

// how each state shapes the orb (tweened, so shapes melt into each other)
const MODES = {
  idle:      { spike: 0.0, ripple: 0.0, twist: 0.0, facet: 0.0, shell: 0.22, spin: 0.15 },
  listening: { spike: 1.0, ripple: 0.15, twist: 0.0, facet: 0.0, shell: 0.45, spin: 0.35 },
  thinking:  { spike: 0.0, ripple: 0.0, twist: 1.0, facet: 0.85, shell: 0.9, spin: 2.2 },
  acting:    { spike: 0.2, ripple: 0.0, twist: 0.45, facet: 0.4, shell: 0.75, spin: 1.2 },
  speaking:  { spike: 0.25, ripple: 1.0, twist: 0.0, facet: 0.0, shell: 0.5, spin: 0.5 },
  waiting:   { spike: 0.3, ripple: 0.2, twist: 0.0, facet: 0.0, shell: 0.4, spin: 0.3 },
  error:     { spike: 0.7, ripple: 0.0, twist: 0.0, facet: 0.6, shell: 0.6, spin: 0.2 },
  muted:     { spike: 0.0, ripple: 0.0, twist: 0.0, facet: 0.3, shell: 0.12, spin: 0.05 },
};

export class Orb {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, premultipliedAlpha: false,
      powerPreference: 'low-power' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    this.renderer.setClearColor(0x000000, 0);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100);
    this.camera.position.z = 5.2;
    const add = THREE.AdditiveBlending;

    this.uniforms = {
      uTime: { value: 0 }, uLevel: { value: 0 }, uSwirl: { value: 0 }, uDim: { value: 1 },
      uSpike: { value: 0 }, uRipple: { value: 0 }, uTwist: { value: 0 }, uFacet: { value: 0 },
      uBands: { value: new Array(32).fill(0) },
      uColorA: { value: new THREE.Color('#0A3D91') }, uColorB: { value: new THREE.Color('#00B7FF') },
    };
    this.sphere = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 36),
      new THREE.ShaderMaterial({ vertexShader: VERT, fragmentShader: FRAG, uniforms: this.uniforms,
        transparent: true, blending: add, depthWrite: false }));
    this.scene.add(this.sphere);

    // soft halo behind, white-hot core inside
    this.glowU = { uColor: { value: new THREE.Color('#00B7FF') }, uLevel: this.uniforms.uLevel, uDim: this.uniforms.uDim };
    const halo = new THREE.Mesh(new THREE.PlaneGeometry(4.8, 4.8), new THREE.ShaderMaterial({ vertexShader: GLOW_VERT,
      fragmentShader: HALO_FRAG, uniforms: this.glowU, transparent: true, blending: add, depthWrite: false }));
    halo.position.z = -1.2;
    this.scene.add(halo);
    this.core = new THREE.Mesh(new THREE.PlaneGeometry(1.5, 1.5), new THREE.ShaderMaterial({ vertexShader: GLOW_VERT,
      fragmentShader: CORE_FRAG, uniforms: this.glowU, transparent: true, blending: add, depthWrite: false }));
    this.core.position.z = 0.2;
    this.scene.add(this.core);

    // wireframe shell
    this.shell = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(1.34, 1)),
      new THREE.LineBasicMaterial({ color: '#7FE9FF', transparent: true, opacity: 0.2, blending: add, depthWrite: false }));
    this.scene.add(this.shell);

    // two orbit rings, one dashed
    const circle = (r, n = 160) => {
      const pts = [];
      for (let i = 0; i <= n; i++) { const a = (i / n) * Math.PI * 2; pts.push(new THREE.Vector3(Math.cos(a) * r, Math.sin(a) * r, 0)); }
      return new THREE.BufferGeometry().setFromPoints(pts);
    };
    this.ring1 = new THREE.Line(circle(1.62), new THREE.LineBasicMaterial({ color: '#9BE7FF', transparent: true,
      opacity: 0.45, blending: add, depthWrite: false }));
    this.ring1.rotation.set(1.25, 0.2, 0);
    this.ring2 = new THREE.Line(circle(1.78), new THREE.LineDashedMaterial({ color: '#9BE7FF', transparent: true,
      opacity: 0.35, dashSize: 0.09, gapSize: 0.07, blending: add, depthWrite: false }));
    this.ring2.computeLineDistances();
    this.ring2.rotation.set(-0.9, 0.6, 0);
    this.scene.add(this.ring1, this.ring2);

    // particle belt
    const N = 420, pos = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) {
      const a = Math.random() * Math.PI * 2, r = 1.48 + Math.random() * 0.38;
      pos[i * 3] = Math.cos(a) * r; pos[i * 3 + 1] = Math.sin(a) * r; pos[i * 3 + 2] = (Math.random() - 0.5) * 0.22;
    }
    const pg = new THREE.BufferGeometry();
    pg.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    this.points = new THREE.Points(pg, new THREE.PointsMaterial({ size: 0.026, color: '#9BE7FF', transparent: true,
      opacity: 0.55, blending: add, depthWrite: false }));
    this.points.rotation.x = 1.15;
    this.scene.add(this.points);

    this.white = new THREE.Color('#ffffff');
    this.tint = new THREE.Color();
    this.target = { a: new THREE.Color('#0A3D91'), b: new THREE.Color('#00B7FF'), swirl: 0, dim: 0.85 };
    this.mode = { ...MODES.idle };
    this.modeTarget = { ...MODES.idle };
    this.level = 0; this.levelTarget = 0;
    this.bands = new Array(32).fill(0); this.bandsTarget = new Array(32).fill(0);
    this.shake = 0; this.fps = 60; this.last = 0; this.clock = new THREE.Clock();
    this.resize();
    requestAnimationFrame((t) => this.frame(t));
  }

  resize() {
    const w = this.canvas.clientWidth || 300, h = this.canvas.clientHeight || 300;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  setPalette(a, b, swirl = 0, dim = 1) {
    this.target.a.set(a); this.target.b.set(b); this.target.swirl = swirl; this.target.dim = dim;
  }

  setMode(state) { this.modeTarget = { ...(MODES[state] || MODES.idle) }; }

  setLevel(rms, bands) {
    this.levelTarget = Math.pow(rms, 1.4);  // keep loud speech from saturating the orb
    if (bands) this.bandsTarget = bands;
  }

  pulse(amount = 0.6) { this.levelTarget = Math.max(this.levelTarget, amount); }
  doShake() { this.shake = 1; }

  frame(t) {
    requestAnimationFrame((tt) => this.frame(tt));
    if (t - this.last < 1000 / this.fps - 2) return;  // fps cap (low when docked and idle)
    this.last = t;
    const dt = Math.min(this.clock.getDelta(), 0.1);
    const u = this.uniforms;
    u.uTime.value += dt;
    // attack fast, release slow
    const k = this.levelTarget > this.level ? 1 - Math.exp(-dt / 0.03) : 1 - Math.exp(-dt / 0.22);
    this.level += (this.levelTarget - this.level) * k;
    this.levelTarget *= Math.exp(-dt / 0.35); // decays when no new levels arrive
    for (let i = 0; i < 32; i++) {
      this.bands[i] += ((this.bandsTarget[i] || 0) - this.bands[i]) * Math.min(1, dt * 14);
      this.bandsTarget[i] = (this.bandsTarget[i] || 0) * Math.exp(-dt / 0.3);
    }
    u.uBands.value = this.bands;
    const breathe = 0.04 * (0.5 + 0.5 * Math.sin(u.uTime.value * 1.4));
    u.uLevel.value = Math.min(1, this.level + breathe);
    const c = 1 - Math.exp(-dt / 0.12);   // ~250 ms colour tween
    const m = 1 - Math.exp(-dt / 0.28);   // ~600 ms shape tween
    u.uColorA.value.lerp(this.target.a, c);
    u.uColorB.value.lerp(this.target.b, c);
    this.glowU.uColor.value.copy(u.uColorB.value);
    u.uSwirl.value += (this.target.swirl - u.uSwirl.value) * c;
    u.uDim.value += (this.target.dim - u.uDim.value) * c;
    for (const key of Object.keys(this.mode)) this.mode[key] += (this.modeTarget[key] - this.mode[key]) * m;
    u.uSpike.value = this.mode.spike; u.uRipple.value = this.mode.ripple;
    u.uTwist.value = this.mode.twist; u.uFacet.value = this.mode.facet;

    this.tint.copy(u.uColorB.value).lerp(this.white, 0.45);
    this.points.material.color.copy(this.tint);
    this.points.material.opacity = (0.25 + this.level * 0.6) * u.uDim.value;
    this.points.rotation.z += dt * (0.12 + u.uSwirl.value * 0.8 + this.level * 0.6);
    const s = 1 + this.level * 0.12;
    this.points.scale.set(s, s, s);

    this.shell.material.color.copy(this.tint);
    this.shell.material.opacity = (this.mode.shell * 0.55 + this.level * 0.2) * u.uDim.value;
    this.shell.rotation.y += dt * this.mode.spin * 0.6;
    this.shell.rotation.x += dt * this.mode.spin * 0.25;
    const sh = 1 + this.level * 0.08 - this.mode.twist * 0.04;
    this.shell.scale.set(sh, sh, sh);

    for (const [ring, dir] of [[this.ring1, 1], [this.ring2, -1]]) {
      ring.material.color.copy(this.tint);
      ring.material.opacity = (0.22 + this.mode.shell * 0.35 + this.level * 0.3) * u.uDim.value;
      ring.rotation.z += dt * dir * (0.25 + this.mode.spin * 0.5);
      const rs = 1 + this.level * 0.1 * (dir > 0 ? 1 : 0.6);
      ring.scale.set(rs, rs, rs);
    }
    this.sphere.rotation.y += dt * (0.1 + u.uSwirl.value * 0.5 + this.mode.spin * 0.15);
    if (this.shake > 0) {
      this.shake = Math.max(0, this.shake - dt * 2.5);
      this.scene.position.x = Math.sin(u.uTime.value * 60) * 0.08 * this.shake;
    } else this.scene.position.x = 0;
    this.renderer.render(this.scene, this.camera);
  }
}
