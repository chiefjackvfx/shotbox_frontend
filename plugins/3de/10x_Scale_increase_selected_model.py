# 3DE4.script.name: 10x Scale increase selected model
# 3DE4.script.version:	v1.0
# 3DE4.script.gui.button: Orientation Controls::10x Scale, align-bottom-left, 80, 20
# Author - https://t.me/vlad_osokin
from vl_sdv import *
from tde4 import *
import os, sys
import os.path

def get_model_rotation_scale(model):
    pg = tde4.getCurrentPGroup()
    matrices = []
    rot_scale_matrix = mat3d(tde4.get3DModelRotationScale3D(pg, model)).trans()
    scale_values_from_matrix = vec3d(rot_scale_matrix[0].norm2(), rot_scale_matrix[1].norm2(), rot_scale_matrix[2].norm2())
    scale_matrix = mat3d(scale_values_from_matrix[0], 0.0, 0.0, 0.0, scale_values_from_matrix[1], 0.0, 0.0, 0.0, scale_values_from_matrix[2])
    rotation_matrix = mat3d(rot_scale_matrix[0].unit(), rot_scale_matrix[1].unit(), rot_scale_matrix[2].unit()).trans()
    matrices.append(rotation_matrix)
    matrices.append(scale_matrix)
    return matrices

def set_model_scale(models, scale_factor):
    pg = tde4.getCurrentPGroup()
    cam = tde4.getCurrentCamera()
    frame = tde4.getCurrentFrame(cam)
    if models:
        for model in models:
            rotation_matrix, scale_matrix = get_model_rotation_scale(model)
            
            new_scale_matrix = mat3d(
                scale_matrix[0][0] * scale_factor, 0.0, 0.0,
                0.0, scale_matrix[1][1] * scale_factor, 0.0,
                0.0, 0.0, scale_matrix[2][2] * scale_factor
            )
            
            final_matrix = rotation_matrix * new_scale_matrix
            tde4.set3DModelRotationScale3D(pg, model, final_matrix.list())

pg = tde4.getCurrentPGroup()
model_list = tde4.get3DModelList(pg, 0)
selected_models = [model for model in model_list if tde4.get3DModelSelectionFlag(pg, model) == 1]

if selected_models:
    set_model_scale(selected_models, 10.0)
else:
    tde4.postQuestionRequester("Error", "ВPlease select at least one model to scale.", "OK")
