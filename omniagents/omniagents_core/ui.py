import flet as ft
from typing import Dict, Optional

# --- Shared Message Class ---
class Message:
    def __init__(self, user_name: str, text: str, message_type: str):
        self.user_name = user_name
        self.text = text
        self.message_type = message_type

# --- Shared Tool Call Display ---
class ToolCallDisplay(ft.Container):
    def __init__(
        self,
        tool_name: str,
        args_str: str,
        icon_map: Optional[Dict[str, str]] = None, # Make icon map optional
        color: str = ft.Colors.BLUE, # Parameterize color
        default_icon: str = ft.Icons.BUILD # Default icon if not in map
    ):
        super().__init__()
        self.padding = ft.padding.only(bottom=2)
        self.margin = ft.margin.only(left=20, right=20, top=8, bottom=12)
        self.animate = ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT)

        # Use provided icon_map or empty dict
        effective_icon_map = icon_map or {}
        icon = effective_icon_map.get(tool_name, default_icon)

        # Prepare icon container
        icon_container = ft.Container(
            content=ft.Icon(name=icon, size=16, color=ft.Colors.WHITE),
            width=28,
            height=28,
            border_radius=14,
            bgcolor=color, # Use parameterized color
            alignment=ft.alignment.center,
        )

        # Prepare text content based on args_str
        if args_str:
            text_content = ft.Column([
                ft.Text(
                    f"Using {tool_name}",
                    weight="w500",
                    size=13,
                    color=ft.Colors.BLACK87
                ),
                ft.Text(
                    f"{args_str}",
                    size=12,
                    color=ft.Colors.BLACK54,
                ),
            ], spacing=2, tight=True, expand=True)
            row_alignment = ft.CrossAxisAlignment.START # Default alignment when args are present
        else:
            text_content = ft.Text(
                f"Using {tool_name}",
                weight="w500",
                size=13,
                color=ft.Colors.BLACK87
            )
            row_alignment = ft.CrossAxisAlignment.CENTER # Center align when no args

        # Construct the main row
        tool_card_content = ft.Row(
            [
                icon_container,
                ft.Container(width=10), # Spacer
                text_content,
            ],
            spacing=0,
            alignment=row_alignment, # Apply conditional alignment
        )

        tool_card = ft.Container(
            content=tool_card_content,
            padding=ft.padding.only(left=12, top=12, bottom=12, right=16),
            border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.04, color), # Use parameterized color
        )

        self.content = tool_card

# --- Shared Tool Output Display ---
class ToolOutputDisplay(ft.Container):
    def __init__(self, output_text):
        super().__init__()
        self.padding = 0
        self.margin = ft.margin.only(left=20, right=20, top=4, bottom=12)
        self.animate = ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT)

        if len(output_text) > 1000:
            display_text = output_text[:1000] + "... [output truncated]"
        else:
            display_text = output_text

        self.content = ft.Container(
            content=ft.Text(
                display_text,
                selectable=True,
                size=13,
                color=ft.Colors.BLACK87,
                font_family="SF Mono, Menlo, Monaco, Consolas, monospace"
            ),
            padding=ft.padding.only(top=12, bottom=12, right=16),
            border_radius=6,
            bgcolor=ft.Colors.with_opacity(0.03, ft.Colors.BLACK),
            border=ft.border.all(0.5, ft.Colors.BLACK12),
        )

# --- Shared Chat Message Display ---
class ChatMessage(ft.Container):
    def __init__(self, message: Message, page: ft.Page = None, assistant_name: str = "Assistant"):
        super().__init__()
        self.animate = ft.animation.Animation(300, ft.AnimationCurve.EASE_OUT)
        self.padding = ft.padding.only(bottom=12)

        is_user = message.user_name == "You"
        is_system = message.user_name == "System"

        if is_user:
            content_area = ft.Container(
                content=ft.Text(message.text, selectable=True, size=15, color=ft.Colors.BLACK87),
                bgcolor=ft.colors.with_opacity(0.05, ft.colors.BLACK),
                border_radius=18,
                padding=ft.padding.symmetric(horizontal=16, vertical=10),
            )
            alignment = ft.MainAxisAlignment.END
            self.content = ft.Row([content_area], alignment=alignment)

        elif is_system:
            content_area = ft.Container(
                content=ft.Text(message.text, selectable=True, size=14, color=ft.Colors.BLACK54, italic=True),
                padding=ft.padding.symmetric(horizontal=16, vertical=8),
            )
            alignment = ft.MainAxisAlignment.START
            self.content = ft.Row([ft.Container(content=content_area, expand=True)], alignment=alignment)

        else: # Assistant message (could be "Assistant" or "Indie", etc.)
            content_area = ft.Container(
                 content=ft.Markdown(
                    value=message.text, selectable=True,
                    extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
                    code_theme=ft.MarkdownCodeTheme.GITHUB,
                    on_tap_link=lambda e: page.launch_url(e.data) if page else None,
                ),
                padding=ft.padding.only(left=20, right=16, top=4, bottom=4),
            )
            alignment = ft.MainAxisAlignment.START
            self.content = ft.Row([ft.Container(content=content_area, expand=True)], alignment=alignment)

# --- Tool Approval Request UI (Moved from MCP Example) ---
class ToolApprovalRequest(ft.Container):
    def __init__(self, tool_name, args_str, on_approve, on_approve_session, on_reject):
        super().__init__()
        self.padding = ft.padding.only(bottom=2)
        self.margin = ft.margin.only(left=20, right=20, top=8, bottom=12)
        self.animate = ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT)

        self.content = ft.Container(
            content=ft.Column([
                ft.Text(
                    f"Approval required: {tool_name}",
                    weight="w500", size=14, color=ft.Colors.ORANGE_800,
                ),
                ft.Text(
                    f"Parameters: {args_str}",
                    size=13, color=ft.Colors.BLACK54, selectable=True,
                ),
                ft.Container(height=10),
                ft.Row([
                    ft.ElevatedButton(
                        text="Approve Once", icon=ft.Icons.CHECK,
                        color=ft.Colors.WHITE, bgcolor=ft.Colors.GREEN_600,
                        on_click=on_approve,
                        height=36, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6))
                    ),
                    ft.ElevatedButton(
                        text="Approve Session", icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
                        color=ft.Colors.WHITE, bgcolor=ft.Colors.BLUE_600,
                        on_click=on_approve_session,
                         height=36, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6))
                    ),
                    ft.ElevatedButton(
                        text="Reject", icon=ft.Icons.CANCEL_OUTLINED,
                        color=ft.Colors.WHITE, bgcolor=ft.Colors.RED_600,
                        on_click=on_reject,
                         height=36, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6))
                    ),
                ], spacing=10, wrap=True), # Allow buttons to wrap
            ]),
            padding=16,
            border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.ORANGE),
            border=ft.border.all(1, ft.Colors.ORANGE_200),
        ) 