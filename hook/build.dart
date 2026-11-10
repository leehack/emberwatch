import 'package:flutter_scene/build_hooks.dart';
import 'package:hooks/hooks.dart';

void main(List<String> args) async {
  await build(args, (input, output) async {
    // flutter_scene:init:start
    // Import .glb and .fscene sources under assets/, loadable by source path
    // with loadScene (and hot-reloadable). A no-op when there are no scenes.
    buildScenes(buildInput: input, buildOutput: output, compressTextures: true);
    // Compile .fmat materials under assets/, loadable by source path with
    // loadFmatMaterial (and hot-reloadable). A no-op when there are none.
    await buildMaterials(buildInput: input, buildOutput: output);
    // flutter_scene:init:end
    const textures = 'assets/textures';
    const surfaces = ['forest_floor', 'bark'];
    buildTextures(
      buildInput: input,
      buildOutput: output,
      textures: [
        for (final surface in surfaces) ...[
          '$textures/${surface}_Diffuse.jpg',
          '$textures/${surface}_nor_gl.jpg',
          '$textures/${surface}_Rough.jpg',
        ],
      ],
      contents: {
        for (final surface in surfaces) ...{
          '$textures/${surface}_nor_gl.jpg': TextureContent.normal,
          '$textures/${surface}_Rough.jpg': TextureContent.data,
        },
      },
      // Color and roughness take the smaller encoding; normal maps lose
      // too much detail in it.
      encodings: {
        for (final surface in surfaces)
          for (final map in ['Diffuse', 'Rough'])
            '$textures/${surface}_$map.jpg': const TextureEncoding.etc1s(
              quality: 160,
            ),
      },
    );
  });
}
