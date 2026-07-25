'use strict';

const Vec3 = {
  add: (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]],
  sub: (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]],
  scale: (a, s) => [a[0] * s, a[1] * s, a[2] * s],
  dot: (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2],
  cross: (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]],
  length: a => Math.hypot(a[0], a[1], a[2]),
  normalise(a) { const n = this.length(a) || 1; return [a[0] / n, a[1] / n, a[2] / n]; },
};

const Mat4 = {
  identity: () => [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
  multiply(a, b) {
    const out = new Array(16).fill(0);
    for (let c = 0; c < 4; c++) {
      for (let r = 0; r < 4; r++) {
        out[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
      }
    }
    return out;
  },
  translation(v) { const m = this.identity(); m[12] = v[0]; m[13] = v[1]; m[14] = v[2]; return m; },
  scaling(v) { const m = this.identity(); m[0] = v[0]; m[5] = v[1]; m[10] = v[2]; return m; },
  fromQuat(q) {
    const [x, y, z, w] = q;
    const x2 = x + x, y2 = y + y, z2 = z + z;
    const xx = x * x2, xy = x * y2, xz = x * z2;
    const yy = y * y2, yz = y * z2, zz = z * z2;
    const wx = w * x2, wy = w * y2, wz = w * z2;
    return [
      1 - (yy + zz), xy + wz, xz - wy, 0,
      xy - wz, 1 - (xx + zz), yz + wx, 0,
      xz + wy, yz - wx, 1 - (xx + yy), 0,
      0, 0, 0, 1,
    ];
  },
  fromTRS(t = [0, 0, 0], r = [0, 0, 0, 1], s = [1, 1, 1]) {
    return this.multiply(this.translation(t), this.multiply(this.fromQuat(r), this.scaling(s)));
  },
  perspective(fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
    return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0];
  },
  lookAt(eye, target, up) {
    const z = Vec3.normalise(Vec3.sub(eye, target));
    const x = Vec3.normalise(Vec3.cross(up, z));
    const y = Vec3.cross(z, x);
    return [
      x[0], y[0], z[0], 0,
      x[1], y[1], z[1], 0,
      x[2], y[2], z[2], 0,
      -Vec3.dot(x, eye), -Vec3.dot(y, eye), -Vec3.dot(z, eye), 1,
    ];
  },
  transformPoint(m, v) {
    const x = v[0], y = v[1], z = v[2];
    const w = m[3] * x + m[7] * y + m[11] * z + m[15] || 1;
    return [
      (m[0] * x + m[4] * y + m[8] * z + m[12]) / w,
      (m[1] * x + m[5] * y + m[9] * z + m[13]) / w,
      (m[2] * x + m[6] * y + m[10] * z + m[14]) / w,
    ];
  },
  transformDirection(m, v) {
    return Vec3.normalise([
      m[0] * v[0] + m[4] * v[1] + m[8] * v[2],
      m[1] * v[0] + m[5] * v[1] + m[9] * v[2],
      m[2] * v[0] + m[6] * v[1] + m[10] * v[2],
    ]);
  },
};

function parseGLB(buffer) {
  const view = new DataView(buffer);
  if (view.getUint32(0, true) !== 0x46546c67 || view.getUint32(4, true) !== 2) throw new Error('The selected file is not a glTF 2.0 GLB.');
  const declared = view.getUint32(8, true);
  if (declared !== buffer.byteLength) throw new Error('The GLB length field does not match the uploaded file.');
  let offset = 12, json = null, bin = null;
  while (offset + 8 <= buffer.byteLength) {
    const length = view.getUint32(offset, true), type = view.getUint32(offset + 4, true);
    const data = buffer.slice(offset + 8, offset + 8 + length);
    if (type === 0x4e4f534a) json = JSON.parse(new TextDecoder().decode(data).replace(/\0+$/g, '').trim());
    if (type === 0x004e4942) bin = data;
    offset += 8 + length;
  }
  if (!json) throw new Error('The GLB does not contain a JSON chunk.');
  return { json, bin: bin || new ArrayBuffer(0) };
}

const COMPONENTS = {
  5120: { ctor: Int8Array, bytes: 1 }, 5121: { ctor: Uint8Array, bytes: 1 },
  5122: { ctor: Int16Array, bytes: 2 }, 5123: { ctor: Uint16Array, bytes: 2 },
  5125: { ctor: Uint32Array, bytes: 4 }, 5126: { ctor: Float32Array, bytes: 4 },
};
const TYPE_SIZE = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 };

function accessorData(doc, bin, accessorIndex) {
  const accessor = doc.accessors?.[accessorIndex];
  if (!accessor || accessor.bufferView === undefined) throw new Error(`Unsupported sparse or absent accessor ${accessorIndex}.`);
  const bufferView = doc.bufferViews?.[accessor.bufferView];
  if (!bufferView) throw new Error(`Missing buffer view ${accessor.bufferView}.`);
  const info = COMPONENTS[accessor.componentType], size = TYPE_SIZE[accessor.type];
  if (!info || !size) throw new Error(`Unsupported accessor type ${accessor.componentType}/${accessor.type}.`);
  const count = accessor.count, stride = bufferView.byteStride || info.bytes * size;
  const start = (bufferView.byteOffset || 0) + (accessor.byteOffset || 0);
  const output = new Float32Array(count * size);
  const data = new DataView(bin);
  const read = (offset) => {
    switch (accessor.componentType) {
      case 5120: return data.getInt8(offset);
      case 5121: return data.getUint8(offset);
      case 5122: return data.getInt16(offset, true);
      case 5123: return data.getUint16(offset, true);
      case 5125: return data.getUint32(offset, true);
      case 5126: return data.getFloat32(offset, true);
      default: return 0;
    }
  };
  const normalise = (value) => {
    if (!accessor.normalized || accessor.componentType === 5126) return value;
    if (accessor.componentType === 5120) return Math.max(value / 127, -1);
    if (accessor.componentType === 5121) return value / 255;
    if (accessor.componentType === 5122) return Math.max(value / 32767, -1);
    if (accessor.componentType === 5123) return value / 65535;
    return value;
  };
  for (let i = 0; i < count; i++) for (let c = 0; c < size; c++) output[i * size + c] = normalise(read(start + i * stride + c * info.bytes));
  return { values: output, count, size, componentType: accessor.componentType };
}

function indexData(doc, bin, accessorIndex, vertexCount) {
  if (accessorIndex === undefined) return Uint32Array.from({ length: vertexCount }, (_, index) => index);
  const raw = accessorData(doc, bin, accessorIndex);
  return Uint32Array.from(raw.values, value => value);
}

function nodeMatrix(node) {
  if (Array.isArray(node.matrix) && node.matrix.length === 16) return node.matrix.slice();
  return Mat4.fromTRS(node.translation, node.rotation, node.scale);
}

function createShader(gl, type, source) {
  const shader = gl.createShader(type); gl.shaderSource(shader, source); gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
  return shader;
}

function createProgram(gl) {
  const vs = `#version 300 es
    precision highp float;
    in vec3 aPosition; in vec3 aNormal; in vec2 aUV;
    uniform mat4 uViewProjection;
    out vec3 vNormal; out vec2 vUV; out vec3 vWorld;
    void main(){ vNormal=aNormal; vUV=aUV; vWorld=aPosition; gl_Position=uViewProjection*vec4(aPosition,1.0); }
  `;
  const fs = `#version 300 es
    precision highp float;
    in vec3 vNormal; in vec2 vUV; in vec3 vWorld;
    uniform sampler2D uTexture; uniform vec4 uFactor; uniform int uMode;
    out vec4 outColor;
    void main(){
      vec4 tex = uMode==1 ? vec4(0.72,0.69,0.64,1.0) : texture(uTexture,vUV)*uFactor;
      vec3 n=normalize(vNormal); float key=max(dot(n,normalize(vec3(0.55,0.35,0.78))),0.0);
      float rim=pow(1.0-max(dot(n,normalize(vec3(1.0,0.0,0.15))),0.0),2.0);
      vec3 lit=tex.rgb*(0.25+0.78*key)+vec3(1.0,0.29,0.02)*rim*0.12;
      outColor=vec4(lit,tex.a);
    }
  `;
  const program = gl.createProgram();
  gl.attachShader(program, createShader(gl, gl.VERTEX_SHADER, vs));
  gl.attachShader(program, createShader(gl, gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
  return program;
}

function rayTriangle(origin, direction, a, b, c) {
  const edge1 = Vec3.sub(b, a), edge2 = Vec3.sub(c, a), p = Vec3.cross(direction, edge2);
  const det = Vec3.dot(edge1, p); if (Math.abs(det) < 1e-8) return null;
  const inv = 1 / det, tvec = Vec3.sub(origin, a), u = Vec3.dot(tvec, p) * inv;
  if (u < 0 || u > 1) return null;
  const q = Vec3.cross(tvec, edge1), v = Vec3.dot(direction, q) * inv;
  if (v < 0 || u + v > 1) return null;
  const t = Vec3.dot(edge2, q) * inv;
  return t > 0.001 ? t : null;
}

function rayAabb(origin, direction, min, max) {
  let tmin = -Infinity, tmax = Infinity;
  for (let i = 0; i < 3; i++) {
    if (Math.abs(direction[i]) < 1e-9) { if (origin[i] < min[i] || origin[i] > max[i]) return false; continue; }
    let a = (min[i] - origin[i]) / direction[i], b = (max[i] - origin[i]) / direction[i];
    if (a > b) [a, b] = [b, a]; tmin = Math.max(tmin, a); tmax = Math.min(tmax, b);
    if (tmax < tmin) return false;
  }
  return tmax >= Math.max(tmin, 0);
}

export class GLBViewer {
  constructor(canvas, markerLayer, skeletonSvg, onAssign) {
    this.canvas = canvas; this.markerLayer = markerLayer; this.skeletonSvg = skeletonSvg; this.onAssign = onAssign;
    this.gl = canvas.getContext('webgl2', { antialias: true, alpha: true });
    if (!this.gl) throw new Error('WebGL 2 is required for the guided 3D workbench.');
    this.program = createProgram(this.gl);
    this.primitives = []; this.rawPrimitives = []; this.landmarks = {}; this.activeLandmark = null;
    this.renderMode = 0; this.bounds = { min: [-1, -1, 0], max: [1, 1, 2], height: 2, width: 2, depth: 2 };
    this.targetHeight = 72; this.frontAxis = 'neg_y';
    this.camera = { yaw: 0, pitch: 0.05, distance: 150, target: [0, 0, 36], pan: [0, 0, 0] };
    this.drag = null; this.lastViewProjection = Mat4.identity(); this.imageUrls = [];
    this._bind(); this.resize(); this.animate();
  }

  destroyModel() {
    const gl = this.gl;
    for (const primitive of this.primitives) {
      if (primitive.vao) gl.deleteVertexArray(primitive.vao);
      for (const buffer of primitive.buffers || []) gl.deleteBuffer(buffer);
      if (primitive.texture) gl.deleteTexture(primitive.texture);
    }
    this.imageUrls.forEach(URL.revokeObjectURL); this.imageUrls = [];
    this.primitives = []; this.rawPrimitives = [];
  }

  async load(buffer, { frontAxis = 'neg_y', targetHeight = 72 } = {}) {
    this.destroyModel();
    const { json: doc, bin } = parseGLB(buffer);
    this.doc = doc; this.bin = bin; this.frontAxis = frontAxis; this.targetHeight = Number(targetHeight) || 72;
    const sceneIndex = doc.scene ?? 0, roots = doc.scenes?.[sceneIndex]?.nodes || doc.nodes?.map((_, index) => index) || [];
    const collected = [];
    const visit = (nodeIndex, parentMatrix) => {
      const node = doc.nodes?.[nodeIndex] || {}, world = Mat4.multiply(parentMatrix, nodeMatrix(node));
      if (node.mesh !== undefined) {
        const mesh = doc.meshes?.[node.mesh];
        for (const primitive of mesh?.primitives || []) collected.push(this._extractPrimitive(primitive, world));
      }
      for (const child of node.children || []) visit(child, world);
    };
    for (const root of roots) visit(root, Mat4.identity());
    if (!collected.length) throw new Error('The GLB contains no triangle mesh primitives.');
    this.rawPrimitives = collected; await this._normaliseAndUpload(); this.viewPreset('front');
    return { triangles: this.primitives.reduce((sum, p) => sum + Math.floor(p.indices.length / 3), 0), bounds: this.bounds };
  }

  _extractPrimitive(primitive, world) {
    if ((primitive.mode ?? 4) !== 4) throw new Error('Only triangle primitives are supported by the guided workbench.');
    const positions = accessorData(this.doc, this.bin, primitive.attributes.POSITION);
    const normals = primitive.attributes.NORMAL !== undefined ? accessorData(this.doc, this.bin, primitive.attributes.NORMAL).values : null;
    const uv = primitive.attributes.TEXCOORD_0 !== undefined ? accessorData(this.doc, this.bin, primitive.attributes.TEXCOORD_0).values : new Float32Array(positions.count * 2);
    const transformed = new Float32Array(positions.count * 3), transformedNormals = new Float32Array(positions.count * 3);
    for (let i = 0; i < positions.count; i++) {
      const p = Mat4.transformPoint(world, [positions.values[i * 3], positions.values[i * 3 + 1], positions.values[i * 3 + 2]]);
      const b = [p[0], -p[2], p[1]];
      transformed.set(b, i * 3);
      const sourceNormal = normals ? [normals[i * 3], normals[i * 3 + 1], normals[i * 3 + 2]] : [0, 0, 1];
      const n = Mat4.transformDirection(world, sourceNormal), bn = Vec3.normalise([n[0], -n[2], n[1]]);
      transformedNormals.set(bn, i * 3);
    }
    return {
      rawPositions: transformed, rawNormals: transformedNormals, uv: new Float32Array(uv),
      indices: indexData(this.doc, this.bin, primitive.indices, positions.count),
      materialIndex: primitive.material ?? -1,
    };
  }

  _orient(v) {
    const [x, y, z] = v;
    if (this.frontAxis === 'neg_y') return [-y, x, z];
    if (this.frontAxis === 'pos_y') return [y, -x, z];
    if (this.frontAxis === 'neg_x') return [-x, -y, z];
    return [x, y, z];
  }

  async _normaliseAndUpload() {
    let min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
    for (const primitive of this.rawPrimitives) {
      for (let i = 0; i < primitive.rawPositions.length; i += 3) {
        const p = this._orient([primitive.rawPositions[i], primitive.rawPositions[i + 1], primitive.rawPositions[i + 2]]);
        for (let c = 0; c < 3; c++) { min[c] = Math.min(min[c], p[c]); max[c] = Math.max(max[c], p[c]); }
      }
    }
    const rawHeight = max[2] - min[2]; if (!(rawHeight > 1e-8)) throw new Error('The GLB has zero usable height.');
    const scale = this.targetHeight / rawHeight, centre = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, min[2]];
    const gl = this.gl; this.primitives = [];
    for (const raw of this.rawPrimitives) {
      const positions = new Float32Array(raw.rawPositions.length), normals = new Float32Array(raw.rawNormals.length);
      const pmin = [Infinity, Infinity, Infinity], pmax = [-Infinity, -Infinity, -Infinity];
      for (let i = 0; i < raw.rawPositions.length; i += 3) {
        const p = this._orient([raw.rawPositions[i], raw.rawPositions[i + 1], raw.rawPositions[i + 2]]);
        const n = Vec3.normalise(this._orient([raw.rawNormals[i], raw.rawNormals[i + 1], raw.rawNormals[i + 2]]));
        const q = [(p[0] - centre[0]) * scale, (p[1] - centre[1]) * scale, (p[2] - centre[2]) * scale];
        positions.set(q, i); normals.set(n, i);
        for (let c = 0; c < 3; c++) { pmin[c] = Math.min(pmin[c], q[c]); pmax[c] = Math.max(pmax[c], q[c]); }
      }
      const material = this.doc.materials?.[raw.materialIndex] || {}, factor = material.pbrMetallicRoughness?.baseColorFactor || [1, 1, 1, 1];
      const texture = await this._createMaterialTexture(material);
      const vao = gl.createVertexArray(); gl.bindVertexArray(vao); const buffers = [];
      const addBuffer = (location, data, size) => { const b = gl.createBuffer(); buffers.push(b); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW); gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location, size, gl.FLOAT, false, 0, 0); };
      addBuffer(gl.getAttribLocation(this.program, 'aPosition'), positions, 3);
      addBuffer(gl.getAttribLocation(this.program, 'aNormal'), normals, 3);
      addBuffer(gl.getAttribLocation(this.program, 'aUV'), raw.uv, 2);
      const index = gl.createBuffer(); buffers.push(index); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, index); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, raw.indices, gl.STATIC_DRAW);
      const edges = new Uint32Array(raw.indices.length * 2);
      for (let i = 0, e = 0; i < raw.indices.length; i += 3) { const a = raw.indices[i], b = raw.indices[i + 1], c = raw.indices[i + 2]; edges.set([a, b, b, c, c, a], e); e += 6; }
      const edgeBuffer = gl.createBuffer(); buffers.push(edgeBuffer); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, edgeBuffer); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, edges, gl.STATIC_DRAW);
      gl.bindVertexArray(null);
      this.primitives.push({ positions, normals, uv: raw.uv, indices: raw.indices, edges, vao, buffers, index, edgeBuffer, texture, factor, min: pmin, max: pmax });
    }
    min = [Infinity, Infinity, Infinity]; max = [-Infinity, -Infinity, -Infinity];
    for (const p of this.primitives) for (let c = 0; c < 3; c++) { min[c] = Math.min(min[c], p.min[c]); max[c] = Math.max(max[c], p.max[c]); }
    this.bounds = { min, max, depth: max[0] - min[0], width: max[1] - min[1], height: max[2] - min[2], scale };
  }

  async _createMaterialTexture(material) {
    const gl = this.gl, texture = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([215, 200, 180, 255]));
    const textureIndex = material.pbrMetallicRoughness?.baseColorTexture?.index;
    const sourceIndex = textureIndex !== undefined ? this.doc.textures?.[textureIndex]?.source : undefined;
    const image = sourceIndex !== undefined ? this.doc.images?.[sourceIndex] : null;
    if (image?.bufferView !== undefined) {
      const view = this.doc.bufferViews?.[image.bufferView], start = view?.byteOffset || 0;
      if (view) {
        const blob = new Blob([this.bin.slice(start, start + view.byteLength)], { type: image.mimeType || 'image/png' });
        const url = URL.createObjectURL(blob); this.imageUrls.push(url);
        try { const bitmap = await createImageBitmap(blob); gl.bindTexture(gl.TEXTURE_2D, texture); gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true); gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, bitmap); gl.generateMipmap(gl.TEXTURE_2D); bitmap.close(); } catch (_) { /* fallback pixel remains */ }
      }
    }
    gl.bindTexture(gl.TEXTURE_2D, null); return texture;
  }

  _bind() {
    this.canvas.addEventListener('pointerdown', event => { this.canvas.setPointerCapture(event.pointerId); this.drag = { x: event.clientX, y: event.clientY, moved: false, button: event.button, shift: event.shiftKey }; });
    this.canvas.addEventListener('pointermove', event => {
      if (!this.drag) return; const dx = event.clientX - this.drag.x, dy = event.clientY - this.drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) this.drag.moved = true;
      if (this.drag.shift || this.drag.button === 1 || this.drag.button === 2) {
        const { right, up } = this.cameraBasis(); const amount = this.camera.distance * 0.0015;
        this.camera.target = Vec3.add(this.camera.target, Vec3.add(Vec3.scale(right, -dx * amount), Vec3.scale(up, dy * amount)));
      } else { this.camera.yaw -= dx * 0.008; this.camera.pitch = Math.max(-1.45, Math.min(1.45, this.camera.pitch + dy * 0.008)); }
      this.drag.x = event.clientX; this.drag.y = event.clientY;
    });
    this.canvas.addEventListener('pointerup', event => {
      const drag = this.drag; this.drag = null;
      if (drag && !drag.moved && event.button === 0 && this.activeLandmark) {
        const hit = this.raycast(event.offsetX, event.offsetY);
        if (hit && this.onAssign) this.onAssign(this.activeLandmark, hit);
      }
    });
    this.canvas.addEventListener('contextmenu', event => event.preventDefault());
    this.canvas.addEventListener('wheel', event => { event.preventDefault(); this.camera.distance = Math.max(12, Math.min(800, this.camera.distance * Math.exp(event.deltaY * 0.001))); }, { passive: false });
    new ResizeObserver(() => this.resize()).observe(this.canvas.parentElement);
  }

  resize() {
    const dpr = Math.min(2, window.devicePixelRatio || 1), rect = this.canvas.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width * dpr)), height = Math.max(1, Math.round(rect.height * dpr));
    if (this.canvas.width !== width || this.canvas.height !== height) { this.canvas.width = width; this.canvas.height = height; }
  }

  cameraBasis() {
    const cp = Math.cos(this.camera.pitch), offset = [Math.cos(this.camera.yaw) * cp, Math.sin(this.camera.yaw) * cp, Math.sin(this.camera.pitch)];
    const position = Vec3.add(this.camera.target, Vec3.scale(offset, this.camera.distance));
    const forward = Vec3.normalise(Vec3.sub(this.camera.target, position)), right = Vec3.normalise(Vec3.cross(forward, [0, 0, 1])), up = Vec3.cross(right, forward);
    return { position, forward, right, up };
  }

  render() {
    this.resize(); const gl = this.gl; gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(0.025, 0.02, 0.015, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); gl.enable(gl.DEPTH_TEST); gl.disable(gl.CULL_FACE);
    const basis = this.cameraBasis(), aspect = this.canvas.width / this.canvas.height;
    const projection = Mat4.perspective(Math.PI / 4, aspect, 0.1, 2000), view = Mat4.lookAt(basis.position, this.camera.target, [0, 0, 1]);
    const vp = Mat4.multiply(projection, view); this.lastViewProjection = vp;
    gl.useProgram(this.program); gl.uniformMatrix4fv(gl.getUniformLocation(this.program, 'uViewProjection'), false, vp);
    gl.uniform1i(gl.getUniformLocation(this.program, 'uTexture'), 0); gl.uniform1i(gl.getUniformLocation(this.program, 'uMode'), this.renderMode === 1 ? 1 : 0);
    for (const primitive of this.primitives) {
      gl.bindVertexArray(primitive.vao); gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, primitive.texture);
      gl.uniform4fv(gl.getUniformLocation(this.program, 'uFactor'), primitive.factor);
      if (this.renderMode === 2) { gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, primitive.edgeBuffer); gl.drawElements(gl.LINES, primitive.edges.length, gl.UNSIGNED_INT, 0); }
      else { gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, primitive.index); gl.drawElements(gl.TRIANGLES, primitive.indices.length, gl.UNSIGNED_INT, 0); }
    }
    gl.bindVertexArray(null); this.updateOverlay(vp);
  }

  animate() { this.render(); requestAnimationFrame(() => this.animate()); }

  project(point, vp) {
    const clip = Mat4.transformPoint(vp, point); return [(clip[0] * 0.5 + 0.5) * this.canvas.clientWidth, (-clip[1] * 0.5 + 0.5) * this.canvas.clientHeight, clip[2]];
  }

  updateOverlay(vp) {
    this.markerLayer.innerHTML = '';
    const projected = {};
    for (const [id, point] of Object.entries(this.landmarks)) {
      const screen = this.project(point, vp); projected[id] = screen;
      const marker = document.createElement('div'); marker.className = `viewport-marker ${id === this.activeLandmark ? 'active' : ''}`;
      marker.style.left = `${screen[0]}px`; marker.style.top = `${screen[1]}px`; marker.title = id;
      marker.innerHTML = `<span>${id.replaceAll('_', ' ')}</span>`; this.markerLayer.appendChild(marker);
    }
    const pairs = [
      ['head_top', 'neck_base'], ['neck_base', 'shoulder_l'], ['neck_base', 'shoulder_r'], ['neck_base', 'pelvis'],
      ['shoulder_l', 'elbow_l'], ['elbow_l', 'wrist_l'], ['shoulder_r', 'elbow_r'], ['elbow_r', 'wrist_r'],
      ['pelvis', 'hip_l'], ['hip_l', 'knee_l'], ['knee_l', 'ankle_l'], ['ankle_l', 'toe_l'],
      ['pelvis', 'hip_r'], ['hip_r', 'knee_r'], ['knee_r', 'ankle_r'], ['ankle_r', 'toe_r'],
    ];
    this.skeletonSvg.innerHTML = pairs.filter(([a, b]) => projected[a] && projected[b]).map(([a, b]) => `<line x1="${projected[a][0]}" y1="${projected[a][1]}" x2="${projected[b][0]}" y2="${projected[b][1]}" />`).join('');
  }

  screenRay(x, y) {
    const basis = this.cameraBasis(), nx = x / this.canvas.clientWidth * 2 - 1, ny = 1 - y / this.canvas.clientHeight * 2;
    const tan = Math.tan(Math.PI / 8), aspect = this.canvas.clientWidth / this.canvas.clientHeight;
    return { origin: basis.position, direction: Vec3.normalise(Vec3.add(basis.forward, Vec3.add(Vec3.scale(basis.right, nx * tan * aspect), Vec3.scale(basis.up, ny * tan)))) };
  }

  raycast(x, y) {
    const ray = this.screenRay(x, y); let nearest = Infinity, point = null;
    for (const primitive of this.primitives) {
      if (!rayAabb(ray.origin, ray.direction, primitive.min, primitive.max)) continue;
      const p = primitive.positions, indices = primitive.indices;
      for (let i = 0; i < indices.length; i += 3) {
        const ia = indices[i] * 3, ib = indices[i + 1] * 3, ic = indices[i + 2] * 3;
        const t = rayTriangle(ray.origin, ray.direction, [p[ia], p[ia + 1], p[ia + 2]], [p[ib], p[ib + 1], p[ib + 2]], [p[ic], p[ic + 1], p[ic + 2]]);
        if (t !== null && t < nearest) { nearest = t; point = Vec3.add(ray.origin, Vec3.scale(ray.direction, t)); }
      }
    }
    return point?.map(value => Number(value.toFixed(5))) || null;
  }

  setLandmarks(landmarks) { this.landmarks = JSON.parse(JSON.stringify(landmarks || {})); }
  setActiveLandmark(id) { this.activeLandmark = id; }
  setMode(mode) { this.renderMode = mode === 'solid' ? 1 : mode === 'wire' ? 2 : 0; }

  viewPreset(name) {
    const h = this.bounds.height || this.targetHeight, centre = [0, 0, h * 0.5]; this.camera.target = centre; this.camera.distance = Math.max(h * 1.6, this.bounds.width * 2.2);
    if (name === 'front') { this.camera.yaw = 0; this.camera.pitch = 0.02; }
    if (name === 'back') { this.camera.yaw = Math.PI; this.camera.pitch = 0.02; }
    if (name === 'left') { this.camera.yaw = Math.PI / 2; this.camera.pitch = 0.02; }
    if (name === 'right') { this.camera.yaw = -Math.PI / 2; this.camera.pitch = 0.02; }
    if (name === 'top') { this.camera.yaw = 0; this.camera.pitch = 1.45; }
  }

  autoSeed() {
    const h = this.bounds.height, w = Math.max(this.bounds.width, h * 0.22), yShoulder = Math.min(w * 0.46, h * 0.22), yWrist = Math.min(w * 0.49, h * 0.48), yHip = Math.min(w * 0.19, h * 0.09);
    return {
      head_top: [0, 0, h * 0.985], chin: [h * 0.045, 0, h * 0.865], neck_base: [0, 0, h * 0.82],
      shoulder_l: [0, yShoulder, h * 0.79], elbow_l: [0, (yShoulder + yWrist) * 0.58, h * 0.785], wrist_l: [0, yWrist, h * 0.78], hand_tip_l: [0, Math.min(w * 0.53, h * 0.53), h * 0.78],
      shoulder_r: [0, -yShoulder, h * 0.79], elbow_r: [0, -(yShoulder + yWrist) * 0.58, h * 0.785], wrist_r: [0, -yWrist, h * 0.78], hand_tip_r: [0, -Math.min(w * 0.53, h * 0.53), h * 0.78],
      pelvis: [0, 0, h * 0.49], hip_l: [0, yHip, h * 0.47], knee_l: [0, yHip * 0.92, h * 0.265], ankle_l: [0, yHip * 0.82, h * 0.055], toe_l: [h * 0.075, yHip * 0.82, h * 0.025],
      hip_r: [0, -yHip, h * 0.47], knee_r: [0, -yHip * 0.92, h * 0.265], ankle_r: [0, -yHip * 0.82, h * 0.055], toe_r: [h * 0.075, -yHip * 0.82, h * 0.025],
      eye_l: [h * 0.047, h * 0.018, h * 0.925], eye_r: [h * 0.047, -h * 0.018, h * 0.925],
    };
  }
}
