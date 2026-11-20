/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the Free
 * Software Foundation; either version 2 of the License, or (at your option)
 * any later version. See LICENSE.txt in the repository root.
 */

package stuefe.dacapo;

import org.dacapo.harness.Callback;
import org.dacapo.harness.CommandLineArgs;

import java.time.ZonedDateTime;

public class JBRCallback extends Callback {

    public JBRCallback(CommandLineArgs args) {
        super(args);
    }

    /* Immediately prior to start of the benchmark */
    @Override
    public void start(String benchmark) {
        ZonedDateTime now = ZonedDateTime.now();
        if (isWarmup()) {
            System.out.printf(">>> Warmup %d started at %s.\n", this.iterations + 1, now.toString());
        } else {
            System.out.printf(">>> Final started at %s.\n", now.toString());
        }
        super.start(benchmark);
    }

}
