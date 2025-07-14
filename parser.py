import os
import json
import clang.cindex
from clang.cindex import Index, Config, CursorKind, TranslationUnit

class KernelCodeAnalyzer:
    def __init__(self, kernel_root, sof_path="sound/soc/sof"):
        Config.set_library_file('/usr/lib/llvm-15/lib/libclang.so')
        self.kernel_root = kernel_root
        self.sof_path = os.path.join(kernel_root, sof_path)
        self.compile_args = self._get_kernel_flags()
        self.modules = {}  # 存储模块数据：{'snd-sof': {'sources':[...], 'symbols':{...}}}

    def _get_kernel_flags(self):
        """提取内核编译参数"""
        with open(os.path.join(self.kernel_root, '.config'), 'r') as f:
            configs = [f'-D{line.strip().replace("=", "")}' for line in f]
        
        return [
            '-nostdinc', '-I./include', '-I./arch/x86/include',
            '-D__KERNEL__', '-DCONFIG_AS_AVX=1', *configs
        ]

    def build_module_map(self):
        """建立模块-源文件映射"""
        makefile = os.path.join(self.sof_path, 'Makefile')
        with open(makefile, 'r') as f:
            for line in f:
                if line.startswith('obj-'):
                    ko_name = line.split('=')[1].strip()[:-3]
                    source_files = next(f).split('+=')[1].split()
                    self.modules[ko_name] = {
                        'sources': [os.path.join(self.sof_path, f) for f in source_files],
                        'symbols': {}
                    }

    def parse_ast(self, filename):
        """解析单个文件的AST"""
        index = Index.create()
        tu = index.parse(filename, args=self.compile_args)
        return tu.cursor

    def extract_symbols(self, cursor, module_name):
        """递归提取符号和位置信息"""
        if cursor.location.file is None: 
            return
            
        file_path = cursor.location.file.name
        if not file_path.startswith(self.kernel_root):
            return
            
        # 符号类型处理
        symbol_key = None
        if cursor.kind == CursorKind.FUNCTION_DECL:
            symbol_key = 'functions'
        elif cursor.kind == CursorKind.STRUCT_DECL:
            symbol_key = 'structures'
        elif cursor.kind == CursorKind.VAR_DECL:
            symbol_key = 'variables'
        elif cursor.kind == CursorKind.ENUM_DECL:
            symbol_key = 'enums'
            
        if symbol_key:
            loc = cursor.location
            symbol_data = {
                'name': cursor.spelling,
                'file': os.path.relpath(file_path, self.kernel_root),
                'line': loc.line,
                'column': loc.column,
                'type': cursor.type.spelling
            }
            self.modules[module_name]['symbols'].setdefault(symbol_key, []).append(symbol_data)
        
        # 递归遍历子节点
        for child in cursor.get_children():
            self.extract_symbols(child, module_name)

    def generate_code_units(self, output_dir):
        """生成代码单元和符号表"""
        os.makedirs(output_dir, exist_ok=True)
        
        for mod_name, data in self.modules.items():
            mod_dir = os.path.join(output_dir, mod_name)
            os.makedirs(mod_dir, exist_ok=True)
            
            # 1. 复制源代码
            for src_file in data['sources']:
                rel_path = os.path.relpath(src_file, self.kernel_root)
                dest_path = os.path.join(mod_dir, 'src', rel_path)
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                os.link(src_file, dest_path)  # 硬链接避免复制
                
                # 解析AST并提取符号
                self.extract_symbols(self.parse_ast(src_file), mod_name)
            
            # 2. 保存符号表
            with open(os.path.join(mod_dir, 'symbols.json'), 'w') as f:
                json.dump(data['symbols'], f, indent=2)
            
            # 3. 保存编译信息
            with open(os.path.join(mod_dir, 'build_info.txt'), 'w') as f:
                f.write(f"Module: {mod_name}\nSources:\n")
                f.write("\n".join(data['sources']))
                f.write("\n\nCompiler flags:\n" + " ".join(self.compile_args))

    def match_dmesg(self, log_line, output_dir):
        """匹配dmesg日志到代码位置"""
        # 示例日志: [    0.483] snd_sof: error: sof_ipc_tx_message: timeout at ops.c:215
        parts = log_line.split(':')
        if len(parts) < 4: 
            return None
            
        module_name = parts[0].strip().split()[-1]
        func_name = parts[-2].split()[-1]
        location = parts[-1].strip()
        
        # 加载符号表
        mod_dir = os.path.join(output_dir, module_name)
        try:
            with open(os.path.join(mod_dir, 'symbols.json'), 'r') as f:
                symbols = json.load(f)
        except:
            return None
            
        # 查找匹配的符号
        result = {}
        for category in ['functions', 'structures', 'variables']:
            for symbol in symbols.get(category, []):
                if symbol['name'] == func_name:
                    result = {
                        'symbol': symbol['name'],
                        'defined_at': f"{symbol['file']}:{symbol['line']}",
                        'log_location': location,
                        'code_path': os.path.join(mod_dir, 'src', symbol['file'])
                    }
                    return result
        return None

# 使用示例
if __name__ == "__main__":
    analyzer = KernelCodeAnalyzer(kernel_root="/path/to/linux-kernel")
    analyzer.build_module_map()
    analyzer.generate_code_units("sof_modules")
    
    # 测试日志匹配
    log = "[    0.483] snd_sof: error: sof_ipc_tx_message: timeout at ops.c:215"
    match = analyzer.match_dmesg(log, "sof_modules")
    print(f"日志匹配结果: {match}")
