import flet as ft
import requests
# import socket # Explicitly import socket to see if it fails directly

def main(page: ft.Page):
    page.title = "Minimal Socket Test"
    page.vertical_alignment = ft.MainAxisAlignment.CENTER

    status_text = ft.Text("Testing network connection...", text_align=ft.TextAlign.CENTER)

    def test_connection(e=None):
        # status_text.value = "Attempting import socket..." # No longer explicit
        # page.update()
        try:
            # # Check if socket import itself works # Removed this section
            # s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # s.close()
            # status_text.value = "socket import successful. Making request..."
            # page.update()

            status_text.value = "Making request..."
            page.update()

            # Now try requests (which will implicitly import socket)
            response = requests.get("https://httpbin.org/get", timeout=10)
            response.raise_for_status() # Raise an exception for bad status codes
            status_text.value = f"Success! Status: {response.status_code}"
        except ImportError as ie:
             status_text.value = f"ImportError: {ie}\nCheck if _socket module is packaged correctly for iOS."
        except requests.exceptions.RequestException as re:
            status_text.value = f"Requests Error: {re}\nCheck network connection and permissions."
        except Exception as ex:
            status_text.value = f"General Error: {ex}"
        page.update()

    page.add(
        ft.Column(
            [
                status_text,
                ft.ElevatedButton("Run Test", on_click=test_connection)
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
        )
    )

    # Run test on load
    test_connection()

if __name__ == "__main__":
    ft.app(target=main) 