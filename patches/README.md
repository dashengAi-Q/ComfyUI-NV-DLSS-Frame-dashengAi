# ReShade compatibility fix

The Linux NR path needs a ReShade fix for calls made through an unwrapped
D3D12 device. ReShade hooks VKD3D's shared device-extension vtable to translate
its virtual descriptor handles. RenoDX also calls those extensions with
descriptors created on the original device. Translating those a second time
can cause an access violation inside `GetCudaIndependentDescriptorObject` or
`GetCudaMergedTextureSamplerObject`.

`reshade-vkd3d-native-descriptors.patch` allows original handles through the
extension hooks. ReShade virtualizes CBV/SRV/UAV and sampler heaps (types 0/1);
VKD3D's original tags (2/3) and handles outside the registered heap table must
remain unchanged. Ordinary ReShade descriptor conversions retain their
existing validation. No NVIDIA DLL is patched.

Source: [crosire/reshade](https://github.com/crosire/reshade) at
`358c345ca2fe64f86e67c694f8379c356627adcb`, under the accompanying BSD-3-Clause
[license](LICENSE-ReShade.txt). The patch is local compatibility work; it is
not represented as an upstream ReShade release.

The `Build Linux-compatible ReShade` workflow checks out that pinned revision,
applies the patch, and builds the add-on enabled x64 DLL using ReShade's MSBuild
configuration. Its `reshade-linux-x64` artifact contains `ReShade64.dll`.
The binary can also be built on Windows with Visual Studio's C++ build tools:

```powershell
git clone --recursive https://github.com/crosire/reshade.git
cd reshade
git checkout 358c345ca2fe64f86e67c694f8379c356627adcb
git submodule update --init --recursive
git apply /path/to/reshade-vkd3d-native-descriptors.patch
msbuild ReShade.sln /m /p:Configuration=Release /p:Platform=64-bit
```

This fixes descriptor handling, not Wine's separate heap compatibility issue.
The Linux guide documents the required environment and the distinction
between NR upscaling and native-resolution NR after the DLSS SR pass.

Install the rebuilt `ReShade64.dll` as `bin/runtime/host-linux/dxgi.dll`.
The Windows carrier in `bin/runtime/host/dxgi.dll` remains the upstream binary.
