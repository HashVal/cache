import os
import re
import json
import shutil
import clang.cindex
from clang.cindex import Index, Config, CursorKind, TranslationUnit

class SOFModuleAnalyzer:
    def __init__(self, kernel_root, sof_path="sound/soc/sof"):
        # 配置 Clang
        Config.set_library_file('/usr/lib/llvm-15/lib/libclang.so')  # 根据您的系统调整
        self.kernel_root = os.path.abspath(kernel_root)
        self.sof_path = os.path.join(self.kernel_root, sof_path)
        self.compile_args = self._get_kernel_flags()
        self.modules = {}  # 存储模块信息

    def _get_kernel_flags(self):
        """提取内核编译参数"""
        flags = [
            '-nostdinc', 
            '-I' + os.path.join(self.kernel_root, 'include'),
            '-I' + os.path.join(self.kernel_root, 'arch', 'x86', 'include'),
            '-D__KERNEL__', 
            '-D__linux__',
            '-DKBUILD_MODNAME="snd_sof"',
            '-DCONFIG_AS_SSSE3=1',
            '-DCONFIG_AS_AVX=1',
            '-DCONFIG_AS_AVX2=1',
            '-fno-strict-aliasing',
            '-fno-common',
            '-fshort-wchar',
            '-Werror=implicit-function-declaration',
            '-Werror=implicit-int',
            '-Werror=return-type',
            '-Werror=strict-prototypes',
            '-Wno-sign-compare',
            '-Wno-frame-address',
            '-Wno-format-truncation',
            '-Wno-format-overflow'
        ]
        
        # 从 .config 添加配置标志
        config_path = os.path.join(self.kernel_root, '.config')
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                for line in f:
                    if line.startswith('CONFIG_'):
                        key, _, value = line.partition('=')
                        if value.strip() == 'y':
                            flags.append(f'-D{key}=1')
                        elif value.strip() == 'm':
                            flags.append(f'-D{key}_MODULE=1')
        
        return flags

    def parse_makefile(self, makefile_path):
        """解析 SOF Makefile 构建模块映射"""
        with open(makefile_path, 'r') as f:
            makefile_content = f.read()
        
        # 解析模块定义
        module_pattern = re.compile(r'obj-\$\(([^)]+)\)\s*\+=\s*([^\s]+)\.o')
        module_matches = module_pattern.findall(makefile_content)
        
        # 解析模块组成
        obj_pattern = re.compile(r'(\w+-sof[-\w]*)-([ym])\s*:?=\s*([^\\]+)', re.MULTILINE)
        obj_matches = obj_pattern.findall(makefile_content)
        
        # 解析条件编译块
        conditional_pattern = re.compile(r'ifneq \(\$\(([^)]+)\),\)\s*(.+?)endif', re.DOTALL)
        conditional_matches = conditional_pattern.findall(makefile_content)
        
        # 构建模块字典
        modules = {}
        for config_var, mod_name in module_matches:
            if mod_name not in modules:
                modules[mod_name] = {
                    'config': config_var,
                    'sources': [],
                    'conditional_sources': {}
                }
        
        # 添加普通源文件
        for mod_prefix, _, sources in obj_matches:
            mod_name = mod_prefix.replace('-y', '').replace('-m', '')
            if mod_name not in modules:
                continue
                
            # 分割源文件列表
            source_list = [s.strip() for s in sources.split() if s.endswith('.o')]
            source_list = [s[:-2] + '.c' for s in source_list]  # 转换为 .c 文件
            
            # 添加到模块
            modules[mod_name]['sources'].extend(source_list)
        
        # 添加条件编译的源文件
        for config_var, content in conditional_matches:
            # 在条件块中查找源文件定义
            cond_obj_matches = obj_pattern.findall(content)
            for mod_prefix, _, sources in cond_obj_matches:
                mod_name = mod_prefix.replace('-y', '').replace('-m', '')
                if mod_name not in modules:
                    continue
                    
                # 分割源文件列表
                source_list = [s.strip() for s in sources.split() if s.endswith('.o')]
                source_list = [s[:-2] + '.c' for s in source_list]  # 转换为 .c 文件
                
                # 添加到条件源
                if config_var not in modules[mod_name]['conditional_sources']:
                    modules[mod_name]['conditional_sources'][config_var] = []
                modules[mod_name]['conditional_sources'][config_var].extend(source_list)
        
        self.modules = modules
        return modules

    def get_module_sources(self, module_name):
        """获取模块的所有源文件（包括条件编译）"""
        base_sources = self.modules[module_name]['sources']
        conditional_sources = []
        
        # 添加条件编译的源文件
        for config_var, sources in self.modules[module_name]['conditional_sources'].items():
            conditional_sources.extend(sources)
        
        # 转换为绝对路径
        all_sources = list(set(base_sources + conditional_sources))
        return [os.path.join(self.sof_path, src) for src in all_sources]

    def parse_file_ast(self, file_path):
        """解析单个文件的 AST"""
        try:
            index = Index.create()
            tu = index.parse(file_path, args=self.compile_args)
            if not tu:
                print(f"解析失败: {file_path}")
                return None
            return tu.cursor
        except Exception as e:
            print(f"解析 {file_path} 时出错: {str(e)}")
            return None

    def extract_symbols(self, cursor):
        """从 AST 中提取符号定义"""
        symbols = {
            'functions': [],
            'structures': [],
            'enums': [],
            'typedefs': [],
            'macros': []
        }
        
        def traverse(cursor):
            if cursor.location.file is None:
                return
                
            file_path = cursor.location.file.name
            if not file_path.startswith(self.kernel_root):
                return
                
            # 记录符号定义
            if cursor.kind == CursorKind.FUNCTION_DECL:
                symbols['functions'].append({
                    'name': cursor.spelling,
                    'file': os.path.relpath(file_path, self.kernel_root),
                    'line': cursor.location.line,
                    'return_type': cursor.result_type.spelling if cursor.result_type else ''
                })
            elif cursor.kind == CursorKind.STRUCT_DECL:
                symbols['structures'].append({
                    'name': cursor.spelling,
                    'file': os.path.relpath(file_path, self.kernel_root),
                    'line': cursor.location.line
                })
            elif cursor.kind == CursorKind.ENUM_DECL:
                symbols['enums'].append({
                    'name': cursor.spelling,
                    'file': os.path.relpath(file_path, self.kernel_root),
                    'line': cursor.location.line
                })
            elif cursor.kind == CursorKind.TYPEDEF_DECL:
                symbols['typedefs'].append({
                    'name': cursor.spelling,
                    'file': os.path.relpath(file_path, self.kernel_root),
                    'line': cursor.location.line
                })
            elif cursor.kind == CursorKind.MACRO_DEFINITION:
                symbols['macros'].append({
                    'name': cursor.spelling,
                    'file': os.path.relpath(file_path, self.kernel_root),
                    'line': cursor.location.line
                })
            
            # 递归遍历子节点
            for child in cursor.get_children():
                traverse(child)
        
        traverse(cursor)
        return symbols

    def generate_module_unit(self, module_name, output_dir):
        """为单个模块生成代码单元"""
        mod_dir = os.path.join(output_dir, module_name)
        src_dir = os.path.join(mod_dir, 'src')
        os.makedirs(src_dir, exist_ok=True)
        
        # 1. 获取模块的所有源文件
        source_files = self.get_module_sources(module_name)
        
        # 2. 复制源文件和头文件
        all_symbols = {'functions': [], 'structures': [], 'enums': [], 'typedefs': [], 'macros': []}
        
        for src_file in source_files:
            if not os.path.exists(src_file):
                print(f"警告: 源文件不存在 {src_file}")
                continue
            
            # 复制源文件
            rel_path = os.path.relpath(src_file, self.kernel_root)
            dest_path = os.path.join(src_dir, rel_path)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(src_file, dest_path)
            
            # 3. 解析 AST 并提取符号
            ast = self.parse_file_ast(src_file)
            if ast:
                file_symbols = self.extract_symbols(ast)
                for key in all_symbols:
                    all_symbols[key].extend(file_symbols[key])
            
            # 4. 复制包含的头文件
            self._copy_included_headers(src_file, src_dir)
        
        # 5. 保存符号表
        with open(os.path.join(mod_dir, 'symbols.json'), 'w') as f:
            json.dump(all_symbols, f, indent=2)
        
        # 6. 保存构建信息
        build_info = {
            'module': module_name,
            'config': self.modules[module_name]['config'],
            'sources': [os.path.relpath(f, self.kernel_root) for f in source_files],
            'conditional_sources': {
                config: [os.path.relpath(f, self.kernel_root) for f in sources]
                for config, sources in self.modules[module_name]['conditional_sources'].items()
            },
            'compiler_flags': self.compile_args
        }
        with open(os.path.join(mod_dir, 'build_info.json'), 'w') as f:
            json.dump(build_info, f, indent=2)
        
        return mod_dir

    def _copy_included_headers(self, src_file, dest_dir):
        """复制源文件中包含的头文件"""
        with open(src_file, 'r') as f:
            content = f.read()
        
        # 查找所有本地包含的头文件
        include_pattern = re.compile(r'#include\s+"([^"]+)"')
        headers = include_pattern.findall(content)
        
        for header in headers:
            # 在 SOF 目录中查找头文件
            header_path = os.path.join(os.path.dirname(src_file), header)
            if os.path.exists(header_path):
                rel_path = os.path.relpath(header_path, self.kernel_root)
                dest_path = os.path.join(dest_dir, rel_path)
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                shutil.copy2(header_path, dest_path)

    def generate_all_units(self, output_dir):
        """为所有模块生成代码单元"""
        # 解析 Makefile
        makefile_path = os.path.join(self.sof_path, 'Makefile')
        self.parse_makefile(makefile_path)
        
        # 为每个模块生成单元
        results = {}
        for module_name in self.modules:
            print(f"生成模块单元: {module_name}")
            mod_dir = self.generate_module_unit(module_name, output_dir)
            results[module_name] = mod_dir
        
        # 生成模块关系图
        self._generate_module_graph(output_dir)
        return results

    def _generate_module_graph(self, output_dir):
        """生成模块依赖关系图（DOT格式）"""
        dot_content = ["digraph SOFModules {"]
        dot_content.append('  rankdir=LR;')
        dot_content.append('  node [shape=box, style=filled, color=lightblue];')
        
        # 添加节点
        for module in self.modules:
            dot_content.append(f'  "{module}";')
        
        # 添加依赖关系
        for module, data in self.modules.items():
            # 主模块依赖
            if module == 'snd-sof':
                for sub_module in self.modules:
                    if sub_module != module and sub_module.startswith('snd-sof'):
                        dot_content.append(f'  "{module}" -> "{sub_module}";')
            
            # 条件依赖
            for config_var in data['conditional_sources']:
                dot_content.append(f'  "{module}" -> "{config_var}" [style=dashed, color=gray];')
        
        dot_content.append("}")
        
        # 保存为DOT文件
        dot_path = os.path.join(output_dir, 'module_dependencies.dot')
        with open(dot_path, 'w') as f:
            f.write("\n".join(dot_content))
        
        print(f"模块依赖图已生成: {dot_path}")

    def find_symbol_definition(self, symbol_name, symbol_type, module_name=None):
        """查找符号定义位置"""
        if module_name:
            modules = [module_name]
        else:
            modules = self.modules.keys()
        
        results = []
        for mod in modules:
            symbols_path = os.path.join(output_dir, mod, 'symbols.json')
            if not os.path.exists(symbols_path):
                continue
            
            with open(symbols_path, 'r') as f:
                symbols = json.load(f)
            
            for item in symbols.get(symbol_type, []):
                if item['name'] == symbol_name:
                    results.append({
                        'module': mod,
                        'file': item['file'],
                        'line': item['line'],
                        'symbol': symbol_name,
                        'type': symbol_type
                    })
        
        return results

# 使用示例
if __name__ == "__main__":
    # 配置参数
    KERNEL_ROOT = "/path/to/linux-kernel"
    OUTPUT_DIR = "/path/to/output/sof_modules"
    
    # 创建分析器
    analyzer = SOFModuleAnalyzer(kernel_root=KERNEL_ROOT)
    
    # 生成所有模块代码单元
    analyzer.generate_all_units(OUTPUT_DIR)
    
    # 示例：查找函数定义
    print("查找函数定义示例:")
    results = analyzer.find_symbol_definition(
        symbol_name="sof_ipc_tx_message",
        symbol_type="functions",
        module_name="snd-sof"
    )
    
    for res in results:
        print(f"找到定义: {res['symbol']} 在 {res['file']}:{res['line']} (模块: {res['module']})")
