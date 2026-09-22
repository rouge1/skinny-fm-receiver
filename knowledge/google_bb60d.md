The official documentation and programming manual for the Signal Hound BB60D Application Programming Interface (API) is distributed as part of the software development kit. The API supports low-level, direct device control and high-performance real-time measurements across the entire BB60 series (BB60D, BB60C, and BB60A). [1, 2] 
## 📖 Official Manuals & Reference Guides

* 
* Online Reference: You can access the structured, interactive version via the [Signal Hound BB60 API Reference](https://signalhound.com/sigdownloads/SDK/online_docs/bb_api/index.html). [1] 
* Theory & Architecture: For a step-by-step breakdown of device communication protocols and core initialization routines, consult the [Signal Hound BB API: Theory of Operation](https://www.google.com/search?q=signal+hound+bb+api:+theory+of+operation&kgmid=/g/11cn3ptpbb). [3] 
* Full PDF Guide: For an offline version covering architecture patterns, system dependencies, and code configuration workflows, download the Signal Hound BB60 Application Programming Interface Manual (PDF). [4] 
* Core SDK Downloads: To acquire the compilation drivers, standard C++ binaries, and functional code implementations, download the comprehensive package directly from the [Signal Hound Software Development Kit (SDK)](https://signalhound.com/software/signal-hound-software-development-kit-sdk/).
* 

------------------------------
## 💡 Core Architectural Framework
The BB60 API is a dynamic link library (bb_api.dll on Windows or libbb_api.so on Linux) that targets 64-bit systems. Programming execution typically follows a fixed structure: [2] 

   1. Initialization: The user connects the hardware via USB 3.0, establishes a solid green status indicator, and calls bbOpenDevice to claim the hardware instance.
   2. Configuration: The application adjusts operational traits such as frequency bands, reference attenuation levels, internal or external 10 MHz references, and trigger orientations via routines like bbConfigureIO.
   3. Mode Assignment: The programmer configures the system to run in standard Swept Analysis, Real-Time FFT Analysis, or High-Speed Streaming I/Q capture mode.
   4. Data Acquisition: The runtime continuously fetches high-density data frames or time-domain blocks using functions like bbFetchRealTimeFrame or I/Q stream buffers.
   5. Teardown: The resource is systematically unwound and safely detached using bbCloseDevice. [3, 4, 5, 6] 


[1] [https://signalhound.com](https://signalhound.com/sigdownloads/SDK/online_docs/bb_api/index.html)
[2] [https://signaltronics.eu](https://signaltronics.eu/all-downloads/bb-downloads/)
[3] [https://signalhound.com](https://signalhound.com/sigdownloads/SDK/online_docs/bb_api/theory_of_operation.html)
[4] [https://signalhound.com](https://signalhound.com/sigdownloads/BB60C/BB60-API-Manual.pdf)
[5] [https://signalhound.com](https://signalhound.com/sigdownloads/BB60C/BB60C-User-Manual.pdf)
[6] [https://signalhound.com](https://signalhound.com/sigdownloads/SDK/bb_api_release_notes.txt)
