//! Static check for a patched engine shader, the way Bevy 0.18.1's shader cache builds it:
//! naga_oil composes the shader with its `#import`ed modules and shader defs, naga validates the
//! result, then the module is lowered to SPIR-V (Vulkan) and HLSL SM 6.0 (DX12) per entry point.
//!
//! wgsl-validate <shader.wgsl> --lib <dir>... [--def NAME | --def NAME=INT | --def NAME=Nu]...
//! Every `.wgsl` under each `--lib` dir that declares `#define_import_path` is importable.

use naga_oil::compose::{
    get_preprocessor_data, ComposableModuleDescriptor, Composer, ImportDefinition,
    NagaModuleDescriptor, ShaderDefValue, ShaderLanguage, ShaderType,
};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
    if let Ok(rd) = std::fs::read_dir(dir) {
        for e in rd.flatten() {
            let p = e.path();
            if p.is_dir() {
                walk(&p, out);
            } else if p.extension().is_some_and(|x| x == "wgsl") {
                out.push(p);
            }
        }
    }
}

fn add_module(
    composer: &mut Composer,
    lib: &HashMap<String, (PathBuf, String)>,
    name: &str,
) -> Result<(), String> {
    if composer.contains_module(name) {
        return Ok(());
    }
    let (path, src) = lib.get(name).ok_or_else(|| format!("import `{name}` not found in --lib"))?;
    let (_, imports, _) = get_preprocessor_data(src);
    for ImportDefinition { import, .. } in &imports {
        add_module(composer, lib, import)?;
    }
    let added = composer.add_composable_module(ComposableModuleDescriptor {
        source: src,
        file_path: &path.to_string_lossy(),
        language: ShaderLanguage::Wgsl,
        ..Default::default()
    });
    if let Err(e) = added {
        return Err(format!("adding {name}: {}", e.emit_to_string(composer)));
    }
    Ok(())
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut shader = None;
    let mut libs = vec![];
    let mut defs = HashMap::new();
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--lib" => {
                libs.push(PathBuf::from(&args[i + 1]));
                i += 1;
            }
            "--def" => {
                let d = &args[i + 1];
                let (k, v) = match d.split_once('=') {
                    None => (d.clone(), ShaderDefValue::Bool(true)),
                    Some((k, v)) if v.ends_with('u') => {
                        (k.to_string(), ShaderDefValue::UInt(v.trim_end_matches('u').parse().unwrap()))
                    }
                    Some((k, v)) => (k.to_string(), ShaderDefValue::Int(v.parse().unwrap())),
                };
                defs.insert(k, v);
                i += 1;
            }
            s => shader = Some(PathBuf::from(s)),
        }
        i += 1;
    }
    let shader = shader.expect("shader path");
    let mut files = vec![];
    for l in &libs {
        walk(l, &mut files);
    }
    let mut lib = HashMap::new();
    for f in files {
        let Ok(src) = std::fs::read_to_string(&f) else { continue };
        if let (Some(name), _, _) = get_preprocessor_data(&src) {
            lib.entry(name).or_insert((f, src));
        }
    }
    let src = std::fs::read_to_string(&shader).expect("read shader");
    let mut composer = Composer::default().with_capabilities(naga::valid::Capabilities::all());
    let (_, imports, _) = get_preprocessor_data(&src);
    for ImportDefinition { import, .. } in &imports {
        if let Err(e) = add_module(&mut composer, &lib, import) {
            eprintln!("FAIL compose-import: {e}");
            std::process::exit(2);
        }
    }
    let module = match composer.make_naga_module(NagaModuleDescriptor {
        source: &src,
        file_path: &shader.to_string_lossy(),
        shader_type: ShaderType::Wgsl,
        shader_defs: defs,
        ..Default::default()
    }) {
        Ok(m) => m,
        Err(e) => {
            eprintln!("FAIL compose: {}", e.emit_to_string(&composer));
            std::process::exit(3);
        }
    };
    let info = match naga::valid::Validator::new(
        naga::valid::ValidationFlags::all(),
        naga::valid::Capabilities::all(),
    )
    .validate(&module)
    {
        Ok(i) => i,
        Err(e) => {
            eprintln!("FAIL validate: {e:?}");
            std::process::exit(4);
        }
    };
    let entries: Vec<String> = module
        .entry_points
        .iter()
        .map(|e| format!("{:?}:{}", e.stage, e.name))
        .collect();
    // SPIR-V, the Vulkan path.
    let spv = naga::back::spv::write_vec(&module, &info, &naga::back::spv::Options::default(), None);
    if let Err(e) = &spv {
        eprintln!("FAIL spv-out: {e:?}");
        std::process::exit(5);
    }
    // HLSL SM 6.0, the DX12 path; wgpu binds via its own map, so let naga fake the registers.
    let mut hlsl = String::new();
    let opts = naga::back::hlsl::Options {
        shader_model: naga::back::hlsl::ShaderModel::V6_0,
        fake_missing_bindings: true,
        ..Default::default()
    };
    let pipeline_opts = Default::default();
    let mut w = naga::back::hlsl::Writer::new(&mut hlsl, &opts, &pipeline_opts);
    if let Err(e) = w.write(&module, &info, None) {
        eprintln!("FAIL hlsl-out: {e:?}");
        std::process::exit(6);
    }
    println!(
        "PASS {} entries=[{}] spirv_words={} hlsl_bytes={}",
        shader.display(),
        entries.join(", "),
        spv.unwrap().len(),
        hlsl.len()
    );
}
