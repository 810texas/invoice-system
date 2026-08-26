// 前端静态校验：用 @vue/compiler-sfc 编译每个 SFC，捕获模板/脚本语法错误。
// 用法：node scripts/check-sfc.mjs
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parse, compileScript, compileTemplate } from 'vue/compiler-sfc'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const srcDir = join(root, 'src')

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name)
    return statSync(full).isDirectory() ? walk(full) : [full]
  })
}

const files = walk(srcDir).filter((f) => f.endsWith('.vue'))
let failed = 0

for (const file of files) {
  const rel = relative(root, file).replace(/\\/g, '/')
  const source = readFileSync(file, 'utf8')
  const { descriptor, errors } = parse(source, { filename: rel })
  const problems = [...errors.map((e) => `parse: ${e.message}`)]

  if (descriptor.script || descriptor.scriptSetup) {
    try {
      compileScript(descriptor, { id: rel })
    } catch (e) {
      problems.push(`script: ${e.message}`)
    }
  }
  if (descriptor.template) {
    const res = compileTemplate({
      source: descriptor.template.content,
      filename: rel,
      id: rel,
      compilerOptions: { bindingMetadata: {} },
    })
    problems.push(...res.errors.map((e) => `template: ${e.message || e}`))
  }

  if (problems.length) {
    failed += 1
    console.log(`[FAIL] ${rel}`)
    problems.forEach((p) => console.log(`        ${p}`))
  } else {
    console.log(`[PASS] ${rel}`)
  }
}

console.log(`\n组件总数 ${files.length}，失败 ${failed}`)
process.exit(failed ? 1 : 0)
