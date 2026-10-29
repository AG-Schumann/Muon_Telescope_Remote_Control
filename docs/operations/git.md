---
title: Fetching the code
parent: Operations
nav_order: 3
---

# Fetching the code

To fetch the whole repository:

```bash
	git clone https://github.com/AG-Schumann/Muon_Telescope_Remote_Control

```

The repository holds the software, the hardware designs and this site. On the Pi only `software/` is needed, and the KiCad and CAD files are large. Git's sparse checkout fetches just that folder while keeping normal `git pull` updates working (an older `scripts-only` branch served this purpose once; it is obsolete and should not be used):


```bash
git clone --no-checkout https://github.com/AG-Schumann/Muon_Telescope_Remote_Control muon
cd muon
git sparse-checkout init --cone
git sparse-checkout set software
# later: git sparse-checkout add docs   (to pull in another folder)
git checkout main
``` 