import os
import re
import ast
import tempfile
from typing import List, Dict, Optional
from pathlib import Path
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import yaml
from PIL import Image, ImageDraw, ImageFont


@register("plugins_summary", "system", "插件功能汇总", "1.0.0")
class PluginsSummary(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.plugins_dir = Path(__file__).parent.parent
        self.plugins_info = []
        self._load_plugins_info()

    def _load_plugins_info(self):
        """加载所有插件的信息"""
        self.plugins_info = []
        
        # 遍历所有插件目录
        try:
            for plugin_name in self.plugins_dir.iterdir():
                plugin_path = self.plugins_dir / plugin_name
                
                # 跳过非目录和当前插件
                if not plugin_path.is_dir() or plugin_name.name == "astrbot_plugin_plugins_summary":
                    continue
            
                plugin_info = {
                    "name": plugin_name.name,
                    "metadata": None,
                    "commands": []
                }
                
                # 读取metadata.yaml
                metadata_path = plugin_path / "metadata.yaml"
                if metadata_path.exists():
                    try:
                        with open(metadata_path, "r", encoding="utf-8") as f:
                            plugin_info["metadata"] = yaml.safe_load(f)
                    except yaml.YAMLError as e:
                        logger.error(f"解析插件 {plugin_name} 的metadata.yaml失败: {str(e)}", exc_info=True)
                    except PermissionError as e:
                        logger.error(f"无权限读取插件 {plugin_name} 的metadata.yaml: {str(e)}", exc_info=True)
                    except IOError as e:
                        logger.error(f"读取插件 {plugin_name} 的metadata.yaml时发生I/O错误: {str(e)}", exc_info=True)
                
                # 解析main.py中的命令
                main_path = plugin_path / "main.py"
                if main_path.exists():
                    try:
                        with open(main_path, "r", encoding="utf-8") as f:
                            content = f.read()
                            plugin_info["commands"] = self._parse_commands(content)
                    except PermissionError as e:
                        logger.error(f"无权限读取插件 {plugin_name} 的main.py: {str(e)}", exc_info=True)
                    except IOError as e:
                        logger.error(f"读取插件 {plugin_name} 的main.py时发生I/O错误: {str(e)}", exc_info=True)
                    except SyntaxError as e:
                        logger.error(f"插件 {plugin_name} 的main.py存在语法错误: {str(e)}", exc_info=True)
                
                self.plugins_info.append(plugin_info)
                
        except PermissionError as e:
            logger.error(f"无权限访问插件目录: {str(e)}", exc_info=True)
        except Exception as e:
            logger.error(f"加载插件列表时发生未预期错误: {str(e)}", exc_info=True)

    def _parse_commands(self, content: str) -> List[Dict]:
        """使用ast模块解析main.py中的命令和描述"""
        commands = []
        
        try:
            # 生成抽象语法树
            tree = ast.parse(content)
            
            # 遍历语法树
            for node in ast.walk(tree):
                # 找到函数定义
                if isinstance(node, ast.AsyncFunctionDef):
                    # 检查是否有 @filter.command 装饰器
                    for decorator in node.decorator_list:
                        # 检查装饰器是否是 filter.command
                        if (isinstance(decorator, ast.Call) and 
                            isinstance(decorator.func, ast.Attribute) and 
                            isinstance(decorator.func.value, ast.Name) and 
                            decorator.func.value.id == 'filter' and 
                            decorator.func.attr == 'command'):
                            
                            # 提取命令名称
                            command_name = ""
                            if decorator.args:
                                arg = decorator.args[0]
                                if isinstance(arg, ast.Constant):
                                    command_name = arg.value
                                elif isinstance(arg, ast.Str):  # 兼容旧版本Python
                                    command_name = arg.s
                            
                            if not command_name:
                                continue
                            
                            # 提取文档字符串
                            docstring = ast.get_docstring(node, clean=True) or ""
                            
                            # 解析描述和用法
                            description = ""
                            usage = ""
                            if docstring:
                                lines = docstring.strip().split('\n')
                                if lines:
                                    description = lines[0].strip()
                                    for line in lines[1:]:
                                        if line.strip().startswith("用法："):
                                            usage = line.strip()
                                            break
                            
                            commands.append({
                                "name": command_name,
                                "description": description,
                                "usage": usage
                            })
        except SyntaxError as e:
            logger.error(f"解析Python代码时发生语法错误: {str(e)}", exc_info=True)
        except Exception as e:
            logger.error(f"解析命令时发生错误: {str(e)}", exc_info=True)
        
        return commands

    @filter.command("插件列表")
    async def show_plugins_list(self, event: AstrMessageEvent):
        '''显示所有插件的列表'''
        self._load_plugins_info()  # 重新加载最新信息
        
        if not self.plugins_info:
            text = "未找到任何插件"
        else:
            result = ["📋 已安装插件列表："]
            for i, plugin in enumerate(self.plugins_info, 1):
                metadata = plugin.get("metadata", {})
                plugin_name = metadata.get("name", plugin["name"])
                plugin_desc = metadata.get("desc", "无描述")
                result.append(f"\n{i}. {plugin_name}")
                result.append(f"   📝 描述：{plugin_desc}")
                
                if plugin.get("commands"):
                    result.append(f"   ⚙️  命令数量：{len(plugin['commands'])}")
            text = "\n".join(result)
        
        # 转换为图片并发送
        img_path = self._text_to_image(text)
        try:
            if img_path:
                yield event.image_result(img_path)
            else:
                yield event.plain_result(text)
        finally:
            # 清理临时文件，确保无论发送是否成功都会执行
            if img_path and os.path.exists(img_path):
                try:
                    os.unlink(img_path)
                except Exception as e:
                    logger.error(f"清理临时文件失败: {str(e)}", exc_info=True)

    @filter.command("插件详情")
    async def show_plugin_details(self, event: AstrMessageEvent):
        '''显示指定插件的详细信息\n用法：/插件详情 插件名称或序号'''
        self._load_plugins_info()  # 重新加载最新信息
        
        args = event.message_str.split(maxsplit=1)
        if len(args) < 2:
            text = "请输入插件名称或序号，例如：/插件详情 追番助手"
        else:
            query = args[1].strip()
            target_plugin = None
            
            # 尝试按序号查找
            try:
                index = int(query) - 1
                if 0 <= index < len(self.plugins_info):
                    target_plugin = self.plugins_info[index]
            except ValueError:
                # 按名称查找
                for plugin in self.plugins_info:
                    metadata = plugin.get("metadata", {})
                    if query in metadata.get("name", "") or query in plugin["name"]:
                        target_plugin = plugin
                        break
            
            if not target_plugin:
                text = f"未找到名称包含 '{query}' 的插件"
            else:
                metadata = target_plugin.get("metadata", {})
                commands = target_plugin.get("commands", [])
                
                result = [
                    f"\n🔍 插件详情：",
                    f"📦 插件ID：{target_plugin['name']}",
                    f"📛 名称：{metadata.get('name', '无')}",
                    f"📝 描述：{metadata.get('desc', '无')}",
                    f"📖 帮助：{metadata.get('help', '无')}",
                    f"🔢 版本：{metadata.get('version', '无')}",
                    f"👤 作者：{metadata.get('author', '无')}",
                    f"🔗 仓库：{metadata.get('repo', '无')}",
                ]
                
                if commands:
                    result.append(f"\n⚙️  命令列表（{len(commands)}个）：")
                    for cmd in commands:
                        result.append(f"\n   📌 命令：{cmd['name']}")
                        result.append(f"   📝 描述：{cmd['description']}")
                        if cmd['usage']:
                            result.append(f"   💡 用法：{cmd['usage']}")
                else:
                    result.append("\n⚙️  命令列表：无")
                
                text = "\n".join(result)
        
        # 转换为图片并发送
        img_path = self._text_to_image(text)
        try:
            if img_path:
                yield event.image_result(img_path)
            else:
                yield event.plain_result(text)
        finally:
            # 清理临时文件，确保无论发送是否成功都会执行
            if img_path and os.path.exists(img_path):
                try:
                    os.unlink(img_path)
                except Exception as e:
                    logger.error(f"清理临时文件失败: {str(e)}", exc_info=True)

    @filter.command("所有命令")
    async def show_all_commands(self, event: AstrMessageEvent):
        '''显示所有插件的命令汇总'''
        self._load_plugins_info()  # 重新加载最新信息
        
        all_commands = []
        for plugin in self.plugins_info:
            metadata = plugin.get("metadata", {})
            plugin_name = metadata.get("name", plugin["name"])
            
            for cmd in plugin.get("commands", []):
                all_commands.append({
                    "plugin": plugin_name,
                    "command": cmd["name"],
                    "description": cmd["description"],
                    "usage": cmd["usage"]
                })
        
        if not all_commands:
            text = "未找到任何命令"
        else:
            # 按插件名称排序
            all_commands.sort(key=lambda x: x["plugin"])
            
            result = [f"📋 所有插件命令汇总（共 {len(all_commands)} 个）："]
            current_plugin = ""
            
            for cmd in all_commands:
                if cmd["plugin"] != current_plugin:
                    result.append(f"\n🔹 {cmd['plugin']}")
                    current_plugin = cmd["plugin"]
                
                result.append(f"   📌 /{cmd['command']}")
                if cmd["description"]:
                    result.append(f"      {cmd['description']}")
                if cmd["usage"]:
                    result.append(f"      💡 {cmd['usage']}")
            
            text = "\n".join(result)
        
        # 转换为图片并发送
        img_path = self._text_to_image(text)
        try:
            if img_path:
                yield event.image_result(img_path)
            else:
                yield event.plain_result(text)
        finally:
            # 清理临时文件，确保无论发送是否成功都会执行
            if img_path and os.path.exists(img_path):
                try:
                    os.unlink(img_path)
                except Exception as e:
                    logger.error(f"清理临时文件失败: {str(e)}", exc_info=True)

    @filter.command("刷新插件列表")
    async def refresh_plugins(self, event: AstrMessageEvent):
        '''刷新插件列表信息'''
        self._load_plugins_info()
        text = "✅ 插件列表已刷新"
        
        # 转换为图片并发送
        img_path = self._text_to_image(text)
        try:
            if img_path:
                yield event.image_result(img_path)
            else:
                yield event.plain_result(text)
        finally:
            # 清理临时文件，确保无论发送是否成功都会执行
            if img_path and os.path.exists(img_path):
                try:
                    os.unlink(img_path)
                except Exception as e:
                    logger.error(f"清理临时文件失败: {str(e)}", exc_info=True)

    def _text_to_image(self, text: str) -> Optional[str]:
        """将文本转换为图片，返回临时图片路径"""
        try:
            # 创建背景图片
            img = Image.new('RGB', (800, 1200), color=(255, 255, 255))
            draw = ImageDraw.Draw(img)
            
            # 尝试获取合适的字体
            font = None
            font_size = 16
            
            # 优先尝试系统字体
            font_candidates = [
                ('/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc', 'Noto Sans CJK'),
                ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf', 'Noto Sans CJK'),
                ('msyh.ttc', 'Microsoft YaHei'),
                ('simhei.ttf', 'SimHei'),
                ('simsun.ttc', 'SimSun')
            ]
            
            for font_path, font_name in font_candidates:
                try:
                    font = ImageFont.truetype(font_path, font_size)
                    logger.debug(f"使用字体: {font_name} - {font_path}")
                    break
                except (IOError, OSError):
                    continue
            
            # 如果所有字体都失败，使用默认字体
            if font is None:
                font = ImageFont.load_default()
                logger.warning("未找到合适的中文字体，使用默认字体，可能无法正确显示中文")
            
            # 绘制文本，支持自动换行
            lines = text.split('\n')
            y = 10
            line_height = 25
            
            for line in lines:
                draw.text((10, y), line, font=font, fill=(0, 0, 0))
                y += line_height
                
                # 如果超出图片高度，结束绘制
                if y > 1180:
                    break
            
            # 安全地创建临时图片文件
            temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            temp_path = temp_file.name
            temp_file.close()
            
            img.save(temp_path, format='PNG')
            return temp_path
        except Exception as e:
            logger.error(f"文本转图片失败: {str(e)}", exc_info=True)
            return None
