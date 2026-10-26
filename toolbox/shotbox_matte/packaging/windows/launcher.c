/*
 * ShotBox Matte portable Windows launcher.
 *
 * Build as an AMD64 Windows GUI executable. It starts only the Python runtime
 * beside this executable, preserves Windows command-line quoting, captures
 * startup diagnostics, and waits so its exit status matches the application.
 * No console, installed Python, shell command, or global environment change.
 */
#ifndef UNICODE
#define UNICODE 1
#endif
#ifndef _UNICODE
#define _UNICODE 1
#endif
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0601
#endif

#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

#define PATH_CHARS 32768u
#define COMMAND_CHARS 32768u

static int show_error(const wchar_t *message, const wchar_t *path, DWORD code) {
    wchar_t system_message[512] = L"";
    wchar_t detail[4096];
    if (code != 0) {
        FormatMessageW(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
                       NULL, code, 0, system_message,
                       (DWORD)(sizeof(system_message) / sizeof(system_message[0])), NULL);
    }
    _snwprintf(detail, sizeof(detail) / sizeof(detail[0]) - 1,
               L"%ls\n\n%ls%ls%ls\n%ls",
               message, path != NULL ? path : L"",
               code != 0 ? L"\nWindows error: " : L"",
               system_message,
               L"Keep ShotBoxMatte.exe together with the complete portable application folder.");
    detail[sizeof(detail) / sizeof(detail[0]) - 1] = L'\0';
    MessageBoxW(NULL, detail, L"ShotBox Matte", MB_OK | MB_ICONERROR | MB_SETFOREGROUND);
    return 1;
}

static int join_path(wchar_t *target, const wchar_t *directory, const wchar_t *name) {
    size_t directory_length = wcslen(directory);
    size_t name_length = wcslen(name);
    if (directory_length + name_length >= PATH_CHARS) {
        return 0;
    }
    memcpy(target, directory, directory_length * sizeof(wchar_t));
    memcpy(target + directory_length, name, (name_length + 1) * sizeof(wchar_t));
    return 1;
}

static int regular_file_exists(const wchar_t *path) {
    DWORD attributes = GetFileAttributesW(path);
    return attributes != INVALID_FILE_ATTRIBUTES && !(attributes & FILE_ATTRIBUTE_DIRECTORY);
}

/* argv[0] has special Windows parsing rules: quotes group spaces, while the
 * remainder is already correctly escaped by the caller. Keep that remainder
 * verbatim rather than decoding and escaping every argument a second time. */
static const wchar_t *argument_tail(const wchar_t *command_line) {
    const wchar_t *cursor = command_line;
    int quoted = 0;
    while (*cursor == L' ' || *cursor == L'\t') {
        ++cursor;
    }
    while (*cursor != L'\0') {
        if (*cursor == L'"') {
            quoted = !quoted;
        } else if (!quoted && (*cursor == L' ' || *cursor == L'\t')) {
            break;
        }
        ++cursor;
    }
    while (*cursor == L' ' || *cursor == L'\t') {
        ++cursor;
    }
    return cursor;
}

static void append_log(HANDLE log, const wchar_t *message) {
    char utf8[4096];
    int length = WideCharToMultiByte(CP_UTF8, 0, message, -1,
                                     utf8, (int)sizeof(utf8), NULL, NULL);
    if (length > 1) {
        DWORD written;
        WriteFile(log, utf8, (DWORD)(length - 1), &written, NULL);
    }
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous_instance,
                   PWSTR command_line_unused, int show_command) {
    (void)instance;
    (void)previous_instance;
    (void)command_line_unused;
    (void)show_command;

    HANDLE heap = GetProcessHeap();
    wchar_t *storage = HeapAlloc(heap, HEAP_ZERO_MEMORY,
                                 5u * PATH_CHARS * sizeof(wchar_t));
    wchar_t *command_line = HeapAlloc(heap, HEAP_ZERO_MEMORY,
                                      COMMAND_CHARS * sizeof(wchar_t));
    if (storage == NULL || command_line == NULL) {
        if (storage != NULL) HeapFree(heap, 0, storage);
        if (command_line != NULL) HeapFree(heap, 0, command_line);
        return show_error(L"Not enough memory to start ShotBox Matte.", NULL,
                          ERROR_NOT_ENOUGH_MEMORY);
    }

    wchar_t *directory = storage;
    wchar_t *python_path = storage + PATH_CHARS;
    wchar_t *bootstrap_path = storage + 2u * PATH_CHARS;
    wchar_t *log_directory = storage + 3u * PATH_CHARS;
    wchar_t *log_path = storage + 4u * PATH_CHARS;
    DWORD module_length = GetModuleFileNameW(NULL, directory, PATH_CHARS);
    int result = 1;
    HANDLE log = INVALID_HANDLE_VALUE;
    HANDLE null_input = INVALID_HANDLE_VALUE;
    LPPROC_THREAD_ATTRIBUTE_LIST attributes = NULL;
    PROCESS_INFORMATION process = {0};

    if (module_length == 0 || module_length >= PATH_CHARS) {
        show_error(L"Cannot locate the ShotBox Matte application folder.", NULL,
                   module_length == 0 ? GetLastError() : ERROR_FILENAME_EXCED_RANGE);
        goto cleanup;
    }
    wchar_t *separator = wcsrchr(directory, L'\\');
    if (separator == NULL) {
        show_error(L"Cannot determine the ShotBox Matte application folder.", directory, 0);
        goto cleanup;
    }
    /* Keep the final separator, including the one in a drive root such as C:\. */
    separator[1] = L'\0';
    if (!join_path(python_path, directory, L"windows_runtime\\python.exe") ||
        !join_path(bootstrap_path, directory, L"windows_bootstrap.py") ||
        !join_path(log_directory, directory, L"logs") ||
        !join_path(log_path, directory, L"logs\\windows-startup.log")) {
        show_error(L"The application folder path is too long.", directory,
                   ERROR_FILENAME_EXCED_RANGE);
        goto cleanup;
    }
    if (!regular_file_exists(python_path)) {
        show_error(L"The bundled Windows Python runtime is missing.", python_path,
                   ERROR_FILE_NOT_FOUND);
        goto cleanup;
    }
    if (!regular_file_exists(bootstrap_path)) {
        show_error(L"The ShotBox Matte startup script is missing.", bootstrap_path,
                   ERROR_FILE_NOT_FOUND);
        goto cleanup;
    }
    if (!CreateDirectoryW(log_directory, NULL)) {
        DWORD error = GetLastError();
        DWORD log_attributes = GetFileAttributesW(log_directory);
        if (error != ERROR_ALREADY_EXISTS || log_attributes == INVALID_FILE_ATTRIBUTES ||
            !(log_attributes & FILE_ATTRIBUTE_DIRECTORY)) {
            show_error(L"Cannot create the startup log folder. Move the portable application to a writable folder.",
                       log_directory, error);
            goto cleanup;
        }
    }

    SECURITY_ATTRIBUTES security = {sizeof(SECURITY_ATTRIBUTES), NULL, TRUE};
    log = CreateFileW(log_path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                       &security, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (log == INVALID_HANDLE_VALUE) {
        show_error(L"Cannot open the ShotBox Matte startup log.", log_path, GetLastError());
        goto cleanup;
    }
    null_input = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                             &security, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (null_input == INVALID_HANDLE_VALUE) {
        show_error(L"Cannot prepare the application input handle.", NULL, GetLastError());
        goto cleanup;
    }

    SYSTEMTIME now;
    wchar_t marker[160];
    GetLocalTime(&now);
    _snwprintf(marker, sizeof(marker) / sizeof(marker[0]),
               L"\r\n[%04hu-%02hu-%02hu %02hu:%02hu:%02hu] ShotBox Matte launcher starting\r\n",
               now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute, now.wSecond);
    append_log(log, marker);

    const wchar_t *tail = argument_tail(GetCommandLineW());
    size_t command_length = wcslen(python_path) + wcslen(bootstrap_path) + wcslen(tail) + 24u;
    if (command_length >= COMMAND_CHARS) {
        show_error(L"The Windows command line is too long.", NULL, ERROR_BAD_LENGTH);
        goto cleanup;
    }
    int formatted = _snwprintf(command_line, COMMAND_CHARS - 1,
                               L"\"%ls\" -u -X utf8 \"%ls\" %ls", python_path, bootstrap_path, tail);
    command_line[COMMAND_CHARS - 1] = L'\0';
    if (formatted < 0) {
        show_error(L"Cannot construct the application command line.", NULL, ERROR_BAD_LENGTH);
        goto cleanup;
    }

    STARTUPINFOEXW startup = {0};
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = null_input;
    startup.StartupInfo.hStdOutput = log;
    startup.StartupInfo.hStdError = log;
    SIZE_T attributes_size = 0;
    InitializeProcThreadAttributeList(NULL, 1, 0, &attributes_size);
    attributes = HeapAlloc(heap, 0, attributes_size);
    if (attributes == NULL ||
        !InitializeProcThreadAttributeList(attributes, 1, 0, &attributes_size)) {
        show_error(L"Cannot prepare the application process.", log_path, GetLastError());
        if (attributes != NULL) HeapFree(heap, 0, attributes);
        attributes = NULL;
        goto cleanup;
    }
    HANDLE inherited_handles[2] = {log, null_input};
    if (!UpdateProcThreadAttribute(attributes, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                   inherited_handles, sizeof(inherited_handles), NULL, NULL)) {
        show_error(L"Cannot configure the startup log handles.", log_path, GetLastError());
        goto cleanup;
    }
    startup.lpAttributeList = attributes;
    if (!CreateProcessW(python_path, command_line, NULL, NULL, TRUE,
                         CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT,
                         NULL, directory, &startup.StartupInfo, &process)) {
        DWORD error = GetLastError();
        append_log(log, L"The bundled Python process could not be created.\r\n");
        show_error(L"Windows could not start the bundled Python runtime. See the startup log.",
                   log_path, error);
        goto cleanup;
    }
    CloseHandle(process.hThread);
    process.hThread = NULL;
    if (WaitForSingleObject(process.hProcess, INFINITE) != WAIT_OBJECT_0) {
        show_error(L"Cannot wait for the ShotBox Matte application.", log_path, GetLastError());
        goto cleanup;
    }
    DWORD exit_code = 1;
    if (!GetExitCodeProcess(process.hProcess, &exit_code)) {
        show_error(L"Cannot read the application's exit status.", log_path, GetLastError());
        goto cleanup;
    }
    wchar_t exit_marker[160];
    _snwprintf(exit_marker, sizeof(exit_marker) / sizeof(exit_marker[0]),
               L"ShotBox Matte exited with code %lu.\r\n", (unsigned long)exit_code);
    append_log(log, exit_marker);
    FlushFileBuffers(log);
    if (exit_code != 0) {
        wchar_t message[256];
        _snwprintf(message, sizeof(message) / sizeof(message[0]),
                   L"ShotBox Matte exited with code %lu. Open the startup log for details.",
                   (unsigned long)exit_code);
        show_error(message, log_path, 0);
    }
    result = (int)exit_code;

cleanup:
    if (process.hThread != NULL) CloseHandle(process.hThread);
    if (process.hProcess != NULL) CloseHandle(process.hProcess);
    if (attributes != NULL) {
        DeleteProcThreadAttributeList(attributes);
        HeapFree(heap, 0, attributes);
    }
    if (null_input != INVALID_HANDLE_VALUE) CloseHandle(null_input);
    if (log != INVALID_HANDLE_VALUE) CloseHandle(log);
    HeapFree(heap, 0, command_line);
    HeapFree(heap, 0, storage);
    return result;
}
