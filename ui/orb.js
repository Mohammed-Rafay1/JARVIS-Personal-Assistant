// The orb: a noise-displaced sphere with fresnel glow, a halo and an orbiting particle ring.
// Driven by: state colour, voice level (rms) and 32 spectrum bands.
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
varying vec3 vNormal; varying vec3 vView; varying float vDisp;
${NOISE}
void main(){
  vec3 p = position;
  float ang = atan(p.y, p.x) / 6.28318 + 0.5;
  int idx = int(clamp(floor(ang * 32.0), 0.0, 31.0));
  float band = uBands[idx];
  float t = uTime * (0.35 + uSwirl * 0.9);
  float n = snoise(p * 1.15 + vec3(t, t * 0.7, -t * 0.5));
  float n2 = snoise(p * 2.4 - vec3(t * 1.3));
  float amp = 0.03 + uLevel * 0.13;
  float d = n * amp + n2 * amp * 0.2 + band * uLevel * 0.1;
  vDisp = d;
  vec3 displaced = p + normal * d;
  vec4 mv = modelViewMatrix * vec4(displaced, 1.0);
  vNormal = normalize(normalMatrix * normal);
  vView = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}`;

const FRAG = /* glsl */`
uniform vec3 uColorA; uniform vec3 uColorB; uniform float uLevel; uniform float uTime; uniform float uDim;
varying vec3 vNormal; varying vec3 vView; varying float vDisp;
void main(){
  float fres = pow(1.0 - max(dot(vNormal, vView), 0.0), 2.2);
  float mixer = clamp(0.5 + vDisp * 3.0 + 0.25 * sin(uTime * 0.8 + vNormal.y * 3.0), 0.0, 1.0);
  vec3 col = mix(uColorA, uColorB, mixer);
  col += fres * mix(uColorB, vec3(1.0), 0.3 + uLevel * 0.35) * 1.1;
  col = mix(col, vec3(1.0), smoothstep(0.7, 1.0, uLevel) * 0.2);  // loud = whiter
  float alpha = (0.55 + fres * 0.45) * uDim;
  gl_FragColor = vec4(col * (0.8 + uLevel * 0.3), alpha);
}`;

const HALO_FRAG = /* glsl */`
uniform vec3 uColor; uniform float uLevel; uniform float uDim; varying vec2 vUv;
void main(){
  float r = length(vUv - 0.5) * 2.0;
  float g = smoothstep(1.0, 0.25, r) * (0.28 + uLevel * 0.55);
  gl_FragColor = vec4(uColor * g, g * uDim);
}`;
const HALO_VERT = /* glsl */`varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`;

export class Orb {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, premultipliedAlpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    this.renderer.setClearColor(0x000000, 0);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100);
    this.camera.position.z = 5.2;

    this.uniforms = {
      uTime: { value: 0 }, uLevel: { value: 0 }, uSwirl: { value: 0 }, uDim: { value: 1 },
      uBands: { value: new Array(32).fill(0) },
      uColorA: { value: new THREE.Color('#1E3A8A') }, uColorB: { value: new THREE.Color('#3B82F6') },
    };
    const sphere = new THREE.Mesh(
      new THREE.IcosahedronGeometry(1, 40),
      new THREE.ShaderMaterial({ vertexShader: VERT, fragmentShader: FRAG, uniforms: this.uniforms,
        transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }));
    this.sphere = sphere;
    this.scene.add(sphere);

    this.haloU = { uColor: { value: new THREE.Color('#3B82F6') }, uLevel: this.uniforms.uLevel, uDim: this.uniforms.uDim };
    const halo = new THREE.Mesh(new THREE.PlaneGeometry(4.6, 4.6),
      new THREE.ShaderMaterial({ vertexShader: HALO_VERT, fragmentShader: HALO_FRAG, uniforms: this.haloU,
        transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }));
    halo.position.z = -1;
    this.scene.add(halo);

    // particle ring
    const N = 520, pos = new Float32Array(N * 3);
    this.seeds = new Float32Array(N);
    for (let i = 0; i < N; i++) {
      const a = Math.random() * Math.PI * 2, r = 1.45 + Math.random() * 0.35;
      pos[i * 3] = Math.cos(a) * r; pos[i * 3 + 1] = Math.sin(a) * r; pos[i * 3 + 2] = (Math.random() - 0.5) * 0.25;
      this.seeds[i] = Math.random();
    }
    const pg = new THREE.BufferGeometry();
    pg.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    this.points = new THREE.Points(pg, new THREE.PointsMaterial({ size: 0.028, color: '#9BE7FF', transparent: true,
      opacity: 0.55, blending: THREE.AdditiveBlending, depthWrite: false }));
    this.points.rotation.x = 1.15;
    this.scene.add(this.points);

    this.target = { a: new THREE.Color('#1E3A8A'), b: new THREE.Color('#3B82F6'), swirl: 0, dim: 0.8 };
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

  setLevel(rms, bands) {
    this.levelTarget = Math.pow(rms, 1.4);  // keep loud speech from saturating the orb
    if (bands) this.bandsTarget = bands;
  }

  pulse(amount = 0.6) { this.levelTarget = Math.max(this.levelTarget, amount); }
  doShake() { this.shake = 1; }

  frame(t) {
    requestAnimationFrame((tt) => this.frame(tt));
    if (t - this.last < 1000 / this.fps - 2) return;  // fps cap (low when docked & idle)
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
    const c = 1 - Math.exp(-dt / 0.12);  // ~250 ms colour tween
    u.uColorA.value.lerp(this.target.a, c);
    u.uColorB.value.lerp(this.target.b, c);
    this.haloU.uColor.value.copy(u.uColorB.value);
    u.uSwirl.value += (this.target.swirl - u.uSwirl.value) * c;
    u.uDim.value += (this.target.dim - u.uDim.value) * c;
    this.points.material.color.copy(u.uColorB.value).lerp(new THREE.Color('#ffffff'), 0.45);
    this.points.material.opacity = (0.25 + this.level * 0.6) * u.uDim.value;
    this.points.rotation.z += dt * (0.12 + u.uSwirl.value * 0.8 + this.level * 0.6);
    const s = 1 + this.level * 0.12;
    this.points.scale.set(s, s, s);
    this.sphere.rotation.y += dt * (0.1 + u.uSwirl.value * 0.5);
    if (this.shake > 0) {
      this.shake = Math.max(0, this.shake - dt * 2.5);
      this.scene.position.x = Math.sin(u.uTime.value * 60) * 0.08 * this.shake;
    } else this.scene.position.x = 0;
    this.renderer.render(this.scene, this.camera);
  }
}
