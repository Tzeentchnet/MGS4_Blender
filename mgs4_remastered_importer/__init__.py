bl_info = {
    "name": "MGS4 Remastered Importer",
    "author": "MGS4 Blender Importer contributors",
    "version": (1, 0, 0),
    "blender": (5, 2, 0),
    "location": "File > Import and View3D > Sidebar > MGS4",
    "description": "Import MGS4 models, rigs, materials, stages, and animations",
    "category": "Import-Export",
}


def register():
    from . import addon

    addon.register()


def unregister():
    from . import addon

    addon.unregister()
