"""Build the original snowy owl mesh and its fallback image in Blender 4.3+."""
import math
import random
import sys
from pathlib import Path

import bpy
from mathutils import Vector

project = Path(__file__).resolve().parents[1]
output = project / "web" / "static" / "avatars"
output.mkdir(parents=True, exist_ok=True)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
rng = random.Random(55)


def material(name, color, roughness=0.65, metallic=0.0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, 1)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    return mat


white = material("Ivory feathers", (0.89, 0.93, 0.98))
cream = material("Warm facial down", (0.99, 0.95, 0.855))
snow = material("Snow tips", (0.97, 0.985, 1))
shade = material("Feather shadow", (0.60, 0.69, 0.81))
wing_color = material("Pearl wing", (0.75, 0.83, 0.94))
mask = material("Facial disk edge", (0.73, 0.80, 0.89))
stripe = material("Pewter markings", (0.22, 0.31, 0.45))
soft_stripe = material("Silver markings", (0.41, 0.51, 0.65))
bill = material("Slate keratin", (0.055, 0.087, 0.13), 0.34)
tip = material("Cool beak tip", (0.12, 0.17, 0.23), 0.32)
mouth = material("Beak interior", (0.054, 0.024, 0.029))
outer_iris = material("Dark amber ring", (0.41, 0.17, 0.028), 0.47)
iris = material("Honey iris", (0.98, 0.49, 0.088), 0.34)
iris_light = material("Gold iris fibers", (1, 0.70, 0.23), 0.38)
iris_dark = material("Bronze iris fibers", (0.59, 0.215, 0.038), 0.38)
pupil = material("Glossy obsidian pupil", (0.008, 0.014, 0.023), 0.17)
glint = material("Eye catchlight", (1, 1, 1), 0.18)
base = material("Frosted mineral", (0.085, 0.16, 0.235), 0.50, 0.16)
base_rim = material("Ice blue edge", (0.17, 0.34, 0.48), 0.46, 0.22)
tuft_colors = (white, snow, wing_color)


def empty(name, parent=None, at=(0, 0, 0)):
    obj = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(obj)
    obj.parent = parent
    obj.location = at
    return obj


def mesh_finish(obj, name, parent, mat):
    obj.name = name
    obj.parent = parent
    obj.data.materials.append(mat)
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    return obj


def oval(name, parent, at, size, mat, tilt=0, segments=24, rings=12):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings, location=at)
    obj = mesh_finish(bpy.context.object, name, parent, mat)
    obj.scale = size
    obj.rotation_euler.y = math.radians(tilt)
    return obj


def tapered(name, parent, a, b, width, depth, mat):
    a, b = Vector(a), Vector(b)
    obj = oval(name, parent, (a + b) / 2, (width, depth, (b - a).length / 2), mat,
               segments=14, rings=8)
    obj.rotation_euler = (b - a).to_track_quat("Z", "Y").to_euler()
    return obj


def beak(name, parent, sections, mat):
    verts, faces = [], []
    n = 16
    for y, z, wh, ht in sections:
        verts.extend((wh * math.cos(i * 2 * math.pi / n), y, z + ht * math.sin(i * 2 * math.pi / n))
                     for i in range(n))
    for row in range(len(sections) - 1):
        for j in range(n):
            start = row * n
            next_j = (j + 1) % n
            faces.append((start + j, start + next_j, start + n + next_j, start + n + j))
    faces.extend([tuple(reversed(range(n))), tuple(range(len(verts) - n, len(verts)))])
    geom = bpy.data.meshes.new(name)
    geom.from_pydata(verts, [], faces)
    geom.update()
    obj = bpy.data.objects.new(name, geom)
    bpy.context.collection.objects.link(obj)
    mesh_finish(obj, name, parent, mat)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    modifier = obj.modifiers.new("Rounded keratin", "SUBSURF")
    modifier.levels = 2


def join_meshes(parent, name):
    meshes = [obj for obj in parent.children if obj.type == "MESH"]
    bpy.ops.object.select_all(action="DESELECT")
    for mesh in meshes:
        mesh.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.join()
    meshes[0].name = name


def animation(obj, attr, points):
    for frame, pose in points:
        setattr(obj, attr, pose)
        obj.keyframe_insert(data_path=attr, frame=frame)


stage = empty("Stage")
for z, depth, radius, finish, name in (
    (0.07, 0.14, 1.08, base, "Perch"),
    (0.127, 0.035, 1.045, base_rim, "Ice rim"),
    (0.154, 0.03, 1.01, base, "Perch surface"),
):
    bpy.ops.mesh.primitive_cylinder_add(vertices=96, radius=radius, depth=depth, location=(0, 0, z))
    obj = mesh_finish(bpy.context.object, name, stage, finish)
    bevel = obj.modifiers.new("Rounded edge", "BEVEL")
    bevel.width = 0.021
    bevel.segments = 3
    obj.modifiers.new("Weighted normals", "WEIGHTED_NORMAL")

root = empty("OwlRoot")
body = empty("BodyPivot", root, (0, 0.07, 0.45))
oval("Body", body, (0, 0.02, 0.65), (0.70, 0.54, 0.87), white, segments=40, rings=24)
oval("Soft belly", body, (0, -0.251, 0.61), (0.57, 0.35, 0.67), snow, segments=32, rings=20)
for side in (-1, 1):
    oval("Leg feathers", root, (side * 0.33, -0.13, 0.32), (0.17, 0.20, 0.21), white)
    for toe in range(3):
        x = side * 0.32 + (toe - 1) * 0.088
        oval("Feathered toe", root, (x, -0.30 + 0.025 * abs(toe - 1), 0.23), (0.072, 0.136, 0.064), cream)
        tapered("Claw", root, (x, -0.37, 0.24), (x, -0.46 + 0.025 * abs(toe - 1), 0.174), 0.029, 0.030, bill)
    oval("Primaries", body, (side * 0.65, 0.09, 0.55), (0.205, 0.42, 0.67), wing_color, -side * 15)
    oval("Wing shoulder", body, (side * 0.61, -0.045, 0.78), (0.23, 0.37, 0.51), white, -side * 18)
    for k in range(8):
        y = 0.26 - 0.067 * k
        x = side * (0.70 + 0.009 * k)
        z = 0.79 - 0.047 * k
        tapered("Flight feather", body, (x, y, z), (x - side * 0.08, y + 0.07, z - 0.67),
                0.10, 0.063, tuft_colors[k % len(tuft_colors)])
        for j in (0.56, 0.78):
            xx, yy, zz = x - side * 0.08 * j, y + 0.07 * j, z - 0.67 * j
            tapered("Wing bar", body, (xx, yy - .028, zz + .019), (xx + .070 * side, yy - .026, zz - .024),
                    .029, .018, stripe if k % 3 == 0 else soft_stripe)
    for i in range(5):
        x = side * (0.11 + 0.081 * i)
        tapered("Tail feather", body, (x, 0.29, 0.56), (x * 1.12, 0.78 - i * 0.026, -0.15 + i * 0.019),
                0.084, 0.06, shade if i % 2 else white)

for row, z in enumerate((0.18, 0.32, 0.46, 0.60, 0.74)):
    for j in range(7):
        x = (j - 3) * .119 + (row % 2) * .032 + rng.uniform(-.028, .028)
        yy = -.25 - .345 * math.sqrt(max(0.03, 1 - (x / .59) ** 2 - ((z - .61) / .69) ** 2))
        oval("Chest covert", body, (x, yy, z + .016), (.089, .036, .103), tuft_colors[(row + j) % 3],
             int(x * 36), segments=14, rings=8)
        if (row + j) % 3 or abs(x) > .27:
            tapered("Snowy owl chest fleck", body, (x - .043, yy - .034, z - .017),
                    (x + .036, yy - .034, z - .008 + rng.uniform(-.03, .03)), .015, .011,
                    stripe if (row + j) % 4 == 0 else soft_stripe)

head = empty("HeadPivot", root, (0, -0.018, 1.70))
oval("Round head", head, (0, 0, .185), (.76, .57, .64), white, segments=48, rings=24)
oval("White chin", head, (0, -.24, -.15), (.63, .397, .29), snow, segments=32, rings=18)
for side in (-1, 1):
    x = side * .30
    oval("Face disk edge", head, (x, -.433, .12), (.39, .185, .43), mask, -side * 17, segments=32)
    oval("Facial disk", head, (x, -.478, .135), (.354, .175, .385), cream, -side * 14, segments=36)
    for index in range(12):
        angle = math.radians(32 + index * 25)
        vx, vz = math.cos(angle), math.sin(angle)
        if side * vx < -.36:
            continue
        tapered("Radial facial feather", head, (x + .19*vx, -.60, .14 + .24*vz),
                (x + .36*vx, -.523, .14 + .40*vz), .039, .025, snow if index % 3 else white)
    tapered("Brow crest", head, (side * .062, -.544, .32), (side * .55, -.535, .46), .093, .094, snow)
    for i in range(6):
        x_pos = side * (.47 + rng.uniform(-.02, .12))
        z_pos = .26 + i * .055
        tapered("Crown detail", head, (x_pos - side * .045, -.295, z_pos),
                (x_pos + side * .055, -.309, z_pos - .026), .018, .021, soft_stripe)
    eye = empty("EyeLeft" if side < 0 else "EyeRight", head, (x, -.628, .142))
    oval("Eye socket", head, (x, -.603, .142), (.181, .08, .172), mask)
    oval("Amber rim", eye, (0, 0, 0), (.150, .069, .147), outer_iris, segments=40, rings=18)
    oval("Iris", eye, (0, -.038, 0), (.132, .046, .13), iris, segments=36, rings=18)
    for j in range(36):
        ang = j * math.tau / 36
        a, b = math.cos(ang), math.sin(ang)
        offset = .105 + rng.uniform(0, .013)
        tapered("Iris fiber", eye, (.077*a, -.078, .077*b), (offset*a, -.061, offset*b),
                .004, .004, iris_light if j % 3 else iris_dark)
    oval("Pupil", eye, (0, -.072, 0), (.085, .036, .087), pupil, segments=36, rings=18)
    oval("Key catchlight", eye, (-.032, -.104, .038), (.026, .012, .023), glint)
    oval("Fill catchlight", eye, (.032, -.104, -.029), (.011, .007, .009), glint)
    join_meshes(eye, "Iris and pupil")
    animation(eye, "scale", [(1,(1,1,1)), (53,(1,1,1)), (57,(1,1,.06)), (61,(1,1,1)),
                             (154,(1,1,1)), (158,(1,1,.09)), (163,(1,1,1)), (216,(1,1,1))])

join_meshes(head, "Head plumage")
join_meshes(body, "Body plumage")
join_meshes(root, "Feet")

oval("Mouth cavity", head, (0, -.623, -.080), (.099, .054, .062), mouth)
beak("UpperBeak", head,
     [(-.47,.014,.109,.080), (-.59,.012,.111,.078), (-.70,.003,.093,.058),
      (-.775,-.021,.062,.039), (-.801,-.060,.035,.019), (-.780,-.086,.003,.003)], bill)
jaw = empty("LowerBeak", head, (0, -.535, -.052))
beak("Mandible", jaw,
     [(0,0,.089,.025), (-.072,-.016,.090,.031), (-.161,-.020,.066,.028),
      (-.217,-.018,.036,.017), (-.249,-.014,.002,.002)], tip)

rad = lambda angles: tuple(math.radians(angle) for angle in angles)
animation(head, "rotation_euler", [(1,rad((0,0,0))), (49,rad((2,-2,-4))), (91,rad((-2,2,4))),
                                    (138,rad((0,-5,1))), (179,rad((2,3,-3))), (216,rad((0,0,0)))])
animation(body, "scale", [(1,(1,1,1)), (57,(1.012,1.017,1.012)), (109,(1,1,1)),
                          (159,(1.012,1.015,1.012)), (216,(1,1,1))])
animation(root, "location", [(1,(0,0,0)), (56,(0,0,.013)), (109,(0,0,0)),
                             (161,(0,0,.011)), (216,(0,0,0))])

scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 216
scene.render.fps = 24
scene.frame_set(1)
bpy.ops.export_scene.gltf(filepath=str(output / "snowy-owl.glb"), export_format="GLB",
                          export_apply=True, export_animations=True, export_animation_mode="SCENE",
                          export_anim_scene_split_object=False)

if "--render-preview" in sys.argv:
    def aim(obj, look):
        obj.rotation_euler = (Vector(look) - obj.location).to_track_quat("-Z", "Y").to_euler()

    def light(name, position, power, size, color):
        bpy.ops.object.light_add(type="AREA", location=position)
        obj = bpy.context.object
        obj.name = name
        obj.data.energy, obj.data.size, obj.data.color = power, size, color
        aim(obj, (0, 0, 1.2))

    bpy.ops.object.camera_add(location=(.27,-7,2.85))
    camera = bpy.context.object
    aim(camera, (0, 0, 1.19))
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 3.06
    scene.camera = camera
    light("Soft key", (-3,-4,5), 430, 4, (1, .89, .76))
    light("Cool fill", (3,-2,3), 220, 3, (.70,.84,1))
    light("Frost rim", (1,3,4), 465, 3, (.54,.77,1))
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 42
    scene.cycles.use_denoising = False
    scene.view_settings.view_transform = "AgX"
    scene.render.resolution_x = scene.render.resolution_y = 640
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.filepath = str(output / "snowy-owl.png")
    bpy.ops.render.render(write_still=True)
