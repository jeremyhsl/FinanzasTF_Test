Para ejecución local seguir la siguiente estructura:


PS C:\Users\jerem\OneDrive\Documentos\Finanzas TP-TF> cd bono_appWeb
PS C:\Users\jerem\OneDrive\Documentos\Finanzas TP-TF\bono_appWeb> Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
PS C:\Users\jerem\OneDrive\Documentos\Finanzas TP-TF\bono_appWeb> .\venv\Scripts\Activate.ps1
>> 
(venv) PS C:\Users\jerem\OneDrive\Documentos\Finanzas TP-TF\bono_appWeb> python app.py

## Personalizar el logo

El logotipo mostrado en la barra de navegación se carga desde `static/logo.png`.
Ese archivo no se incluye en este repositorio debido a restricciones con archivos binarios, por lo que deberás colocar tu propia imagen con ese nombre.
Si cambias el contenido de `static/logo.png`, tu logo se mostrará automáticamente. La referencia al archivo está en `templates/base.html`.
