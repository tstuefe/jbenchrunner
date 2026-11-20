# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

jbr-filt -t dacapo-sunday-run | jbr-filt --or --gc g1gc --gc gencon | jbr-ls  -o name,heapszf,heapsz,jdkname,gc,result_ms,rssavg  -s name,heapszf,jdkname,result_ms --sort-asc -g name,heapszf --compare result_ms,rssavg --color
